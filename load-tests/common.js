import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Rate, Gauge, Trend } from 'k6/metrics';

const contractErrors = new Rate('contract_errors');
const scenarioStart = new Gauge('benchmark_scenario_started_at_ms');
const completionElapsed = new Trend('benchmark_completion_elapsed_ms');
const clockDrift = new Trend('benchmark_wall_clock_drift_ms');
const durationParts = (__ENV.DURATION || '120s').match(/^(\d+(?:\.\d+)?)(s|m|h)$/);
const scheduledSeconds = Number(durationParts[1]) * {s:1, m:60, h:3600}[durationParts[2]];
export const options = {
  scenarios: { load: {
    executor: 'constant-arrival-rate', rate: Number(__ENV.TARGET_RPS || 100), timeUnit: '1s',
    duration: __ENV.DURATION || '120s', preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 100),
    maxVUs: Number(__ENV.MAX_VUS || 2000), gracefulStop: '10s',
  }},
  summaryTrendStats: ['avg','min','med','max','p(90)','p(95)','p(99)'],
  thresholds: { contract_errors: ['rate<0.01'] },
  discardResponseBodies: false,
  // 'url' and 'vu' are deliberately absent: /users/:id with 100,000 distinct IDs
  // creates one metric time series per URL and exhausts k6's memory at ~1,000 RPS.
  // Requests are grouped by a fixed route 'name'; the VU identity needed by
  // sustained-load analysis travels as metric metadata, which creates no series.
  systemTags: ['proto','subproto','status','method','name','group','check','error','error_code','tls_version','scenario','service','expected_response'],
};
const routes = {plaintext:'GET /plaintext',json:'GET /json','json-large':'GET /json-large',compute:'GET /compute',
  'db-read':'GET /users/:id','db-list':'GET /users','db-create':'POST /users','db-update':'PUT /users/:id'};
const base = __ENV.BASE_URL;
const seed = Number(__ENV.RANDOM_SEED || 20260930);
function hash(n) { n = (n ^ seed) >>> 0; n = Math.imul(n ^ (n >>> 16), 0x45d9f3b); n = Math.imul(n ^ (n >>> 16), 0x45d9f3b); return (n ^ (n >>> 16)) >>> 0; }
export function run(scenario) {
  const iteration = exec.scenario.iterationInTest;
  if (iteration === 0) scenarioStart.add(exec.scenario.startTime);
  const id = 1 + hash(iteration) % 100000;
  if (scenario === 'mixed') {
    const choice = hash(iteration + 1000000) % 100;
    scenario = choice < 40 ? 'json' : choice < 70 ? 'db-read' : choice < 85 ? 'db-list' : choice < 95 ? 'compute' : 'db-create';
  }
  exec.vu.metrics.metadata.vu = String(exec.vu.idInTest);
  const params = { tags: { endpoint: scenario, name: routes[scenario] }, headers: { 'Content-Type': 'application/json' }, timeout: '10s' };
  let response, expected = 200;
  // k6 computes progress from Go's monotonic time.Since(scenarioStart).
  // Date.now() can jump when Docker Desktop synchronizes its VM clock.
  const requestStartedSeconds = exec.scenario.progress * scheduledSeconds;
  const paths = {plaintext:'/plaintext',json:'/json','json-large':'/json-large',compute:'/compute','db-read':`/users/${id}`,'db-list':'/users?limit=100'};
  if (scenario === 'db-create') {
    expected = 201;
    response = http.post(`${base}/users`, JSON.stringify({name:'Benchmark User',email:`${__ENV.RUN_TOKEN}-${iteration}@example.test`}), params);
  } else if (scenario === 'db-update') {
    response = http.put(`${base}/users/${id}`, JSON.stringify({name:'Updated User'}),params);
  } else response = http.get(base + paths[scenario], params);
  let valid = response.status === expected;
  if (valid) {
    try {
      if (scenario === 'plaintext') valid = response.body === 'Hello, World!';
      else {
        const data=response.json();
        if (scenario === 'json') valid=data.message==='Hello, World!';
        if (scenario === 'compute') valid=data.result===4800245;
        if (scenario === 'db-read' || scenario === 'db-update') valid=data.id===id;
        if (scenario === 'db-list') valid=Array.isArray(data)&&data.length===100&&data[0].id===1&&data[99].id===100;
        if (scenario === 'db-create') valid=data.name==='Benchmark User'&&data.id>100000;
        if (scenario === 'json-large') valid=data.items&&data.items.length===800;
      }
    } catch (_) { valid=false; }
  }
  check(response, {'API contract':()=>valid});
  contractErrors.add(!valid);
  const completedMs = requestStartedSeconds * 1000 + response.timings.blocked + response.timings.duration;
  completionElapsed.add(completedMs);
  clockDrift.add(Date.now() - exec.scenario.startTime - completedMs);
}
export function handleSummary(data) {
  const started = data.metrics.benchmark_scenario_started_at_ms.values.value;
  const elapsed = Math.max(scheduledSeconds, data.metrics.benchmark_completion_elapsed_ms.values.max / 1000);
  data.benchmark_window = {method:'scenario-monotonic-v2', started_at_ms:started,
    finished_at_ms:started + elapsed * 1000, duration_seconds:elapsed,
    scheduled_duration_seconds:scheduledSeconds,
    wall_clock_drift_min_ms:data.metrics.benchmark_wall_clock_drift_ms.values.min,
    wall_clock_drift_max_ms:data.metrics.benchmark_wall_clock_drift_ms.values.max};
  return { [__ENV.SUMMARY_PATH || '/results/k6-summary.json']: JSON.stringify(data,null,2) };
}

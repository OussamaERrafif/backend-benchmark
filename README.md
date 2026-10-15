# Backend Framework Benchmark

A reproducible, fully Dockerized benchmark that compares eight backend frameworks on
**throughput, tail latency, CPU, memory and PostgreSQL performance** under identical
resource limits, on a single machine.

The goal is not a leaderboard. Every implementation exposes the same API, talks to the
same database through a thin driver, and is measured with the same open-model load. Every
failure is kept and labelled with the component that actually failed.

| ID | Framework | Runtime | Database driver | Server / workers |
| --- | --- | --- | --- | --- |
| `springboot` | Spring Boot 3.4.4 | Java 21 (Temurin) | JdbcTemplate + HikariCP | Embedded Tomcat |
| `fastapi` | FastAPI 0.115.12 | Python 3.12 | asyncpg | Uvicorn, 1 process per CPU |
| `laravel` | Laravel 12.3 | PHP 8.3 | Query Builder (PDO) | FrankenPHP classic mode, 1 thread per CPU |
| `express` | Express 4.21 | Node.js 22 | node-postgres | `node:cluster`, 1 worker per CPU |
| `fastify` | Fastify 5.2 | Node.js 22 | node-postgres | `node:cluster`, 1 worker per CPU |
| `aspnet` | ASP.NET Core 8.0 | .NET 8 | Npgsql | Kestrel |
| `fiber` | Fiber 2.52 | Go 1.24 | pgx | `GOMAXPROCS` = CPUs |
| `axum` | Axum 0.8 | Rust 1.85 | sqlx | Tokio, worker threads = CPUs |

Exact image tags, runtime versions and pool settings are pinned in [`versions.lock`](versions.lock).
No image uses `latest`.

## Requirements

Docker Engine or Docker Desktop with Compose v2, and Git. Nothing else is installed on the
host: builds, PostgreSQL, k6, orchestration and analysis all run in containers.

| Profile | Backend | PostgreSQL | k6 | Minimum Docker resources |
| --- | --- | --- | --- | --- |
| `small` | 1 CPU / 512 MiB | 1 CPU / 1 GiB | 1 CPU / 512 MiB | 3 CPUs, 3 GiB |
| `standard` | 2 CPU / 2 GiB | 1 CPU / 1 GiB | 2 CPU / 1 GiB | 5 CPUs, 5 GiB |
| `large` | 4 CPU / 4 GiB | 2 CPU / 2 GiB | 2 CPU / 1 GiB | 8 CPUs, 8 GiB |

Limits are enforced through Compose (`cpus`, `mem_limit`, `memswap_limit`) and verified by
inspecting each container before measurement. `doctor` refuses profiles that would
overcommit the Docker VM.

## Quick start

```bash
cp .env.example .env
bash scripts/doctor.sh            # Docker, CPU, RAM, disk, ports
bash scripts/build.sh             # build all eight images
bash scripts/smoke-test.sh        # 22 API contract checks per framework

BENCH_MODE=quick SCENARIOS=json bash scripts/benchmark-all.sh small
docker compose run --rm analysis  # CSVs, charts and Markdown/HTML report
```

Windows PowerShell has equivalent launchers:

```powershell
Copy-Item .env.example .env
.\scripts\doctor.ps1
.\scripts\build.ps1
.\scripts\smoke-test.ps1
$env:BENCH_MODE = 'quick'; $env:SCENARIOS = 'json'
.\scripts\benchmark-all.ps1 small
docker compose run --rm analysis
```

Benchmark a single framework with `scripts/benchmark.sh fastapi standard`. Quick mode
(10 s warm-up, 20 s measurement, one repetition, 100/500/1000 RPS) is for debugging only
and is excluded from the research report.

## Full research campaign

```bash
bash scripts/research.sh          # or .\scripts\research.ps1
docker logs --tail 30 backend-benchmark-research
```

The campaign runs as a detached Docker worker. It covers all eight frameworks and all nine
workloads on the `standard` profile and can take several days. It checkpoints every
measurement, resumes after Docker or host restarts, and regenerates the report between load
levels. Before marking itself complete, it runs an integrity check covering repetitions,
sustained tests, chart points and report links. On Windows, the launcher holds a
temporary sleep-prevention request while the worker runs, and
`.\scripts\research-status.ps1` shows the live phase.

Do not rebuild images, run another benchmark or run Compose cleanup while a campaign is
active.

## Workloads

| Scenario | Request | Measures |
| --- | --- | --- |
| `plaintext` | `GET /plaintext` → `Hello, World!` | HTTP and routing overhead |
| `json` | `GET /json` → `{"message":"Hello, World!"}` | Small JSON serialization |
| `json-large` | `GET /json-large` → shared ~110 KB fixture (800 items) | Serialization and memory pressure |
| `compute` | `GET /compute` → 100,000-step modular loop, computed per request | CPU-bound work |
| `db-read` | `GET /users/:id` (random id among 100,000) | Indexed point read |
| `db-list` | `GET /users?limit=100` | 100-row read and serialization |
| `db-create` | `POST /users` with a unique email | Insert with unique constraint |
| `db-update` | `PUT /users/:id` | Update with `RETURNING` |
| `mixed` | 40% json, 30% db-read, 15% db-list, 10% compute, 5% db-create | Seeded, deterministic mix |

### API contract

- JSON responses are compared structurally, so property order and whitespace don't matter.
- `/users` returns users ordered by ID. `limit` defaults to 100, and values outside 1–100
  return `400 {"error":"invalid request"}`.
- A missing user returns `404 {"error":"not found"}`. A duplicate email returns
  `409 {"error":"conflict"}`.
- Names and emails must be non-empty, with at most 100 and 255 Unicode characters.
- User responses contain only `id`, `name` and `email`.
- Every SQL statement binds its parameters.
- Nothing is cached: the fixture is serialized and the computation runs on every request.
- No reverse proxy, debug mode or request logging is used.

## Methodology

- **One backend at a time**, against a deterministic 100,000-row PostgreSQL 17 database.
  The database is reset before and between write repetitions.
- **Open-model load** uses the k6 `constant-arrival-rate` executor. The load ladder is
  50, 100, 250, 500, 1000, 2000, 4000 and 8000 RPS.
- **Every repetition** has 60 s warm-up, 120 s measurement and 30 s cooldown. Three
  repetitions run at every visited level, and the framework order is shuffled with a seed
  for each repetition.
- **Sustainable level:** all repetitions have errors < 1%, p95 < 100 ms, p99 < 250 ms
  and achieved throughput ≥ 98% of the target. All thresholds can be changed in `.env`.
- **Adaptive stop:** escalation stops after two consecutive non-sustainable levels, or
  after a container failure.
- **Sustained test:** 10 minutes at 80% of the highest sustainable level, tracking
  memory growth, p99 drift and throughput stability.
- **Resources** are sampled every second for the backend, PostgreSQL and k6. Throughput
  uses k6's monotonic clock, so VM clock adjustments don't distort rates.
- **Secondary metrics:** startup time to the first healthy `/health` (5 samples) and image
  size.

### Outcome states

Results are never replaced with zeros. Each run gets exactly one state:

| State | Meaning |
| --- | --- |
| `PASS` | Met every threshold |
| `LATENCY_LIMIT` / `ERROR_LIMIT` / `THROUGHPUT_LIMIT` | Backend missed a threshold |
| `LOAD_GENERATOR_LIMITED` | k6 used ≥ 90% of its CPU quota and could not deliver the target |
| `DATABASE_LIMITED` | PostgreSQL was saturated while the backend was not |
| `OUT_OF_MEMORY` / `BACKEND_CRASH` | The **backend** container was OOM-killed or exited |
| `LOAD_GENERATOR_OOM` / `DATABASE_OOM` | **k6** or **PostgreSQL** was OOM-killed; backend capacity unresolved |
| `HEALTH_CHECK_FAILED` / `BENCHMARK_ERROR` | Invalid evidence, kept for inspection |

## Output

| Path | Contents |
| --- | --- |
| `results/raw/<session>/` | k6 summaries, per-second Docker stats, container logs and metadata for every attempt (append-only) |
| `results/processed/` | `results.csv`, `summary.csv`, `max-sustainable-rps.csv`, coverage, provenance, stress windows |
| `reports/` | `benchmark-report.md` / `.html` and charts generated directly from processed data |

Generated data is ignored by Git. `docker compose --profile '*' down` removes containers,
and `-v` also resets the database volume. Neither command deletes results.

## Repository layout

```text
apps/          eight implementations (plus shared Node code in node-common.mjs)
database/      schema, deterministic seed and reset SQL
fixtures/      shared large JSON document
load-tests/    k6 scripts; common.js holds the shared request and validation logic
scripts/       doctor/build/smoke/benchmark launchers (sh + ps1), runner.py, campaign.py
analysis/      result validation, aggregation, charts and report generation
```

Run the regression tests inside the tools image, or locally with pandas and matplotlib.
Tests that need real measurements skip when `results/raw` is empty.

```bash
cd analysis && python -m unittest test_analysis test_research_report
cd scripts  && PYTHONPATH=../analysis python -m unittest test_campaign_reporting test_resume_reports test_investigated_attempts test_recover_suspended
```

## Limitations

- **Single machine.** Backend, database, Docker and k6 share one host, so compare frameworks
  relative to each other rather than reading the numbers as absolute capacities.
- **Generator ceiling.** With 2 CPUs, k6 tops out at roughly 4,000 RPS on the small
  endpoints. At that point several frameworks are still well below their CPU limit, so a
  shared highest passing level is a harness bound, not a tie. Give k6 more CPU, or use the
  `small` profile, to find backend saturation points.
- **Laravel** runs in FrankenPHP classic mode and boots the framework on every request. Its
  persistent PDO connections are bounded by the PHP thread count instead of a pool of 20.
  Octane or worker mode is not measured.
- **Scaling** (1 → 2 → 4 CPUs) needs separate campaigns per profile. The `large` profile
  needs at least 8 GiB of Docker memory.
- Results apply to the pinned versions, configuration and workloads only. They do not
  establish a universal ranking.

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$campaignPath = Join-Path $workspace 'verification/research-campaign.json'
if (-not (Test-Path -LiteralPath $campaignPath)) { Write-Output 'Research campaign has not started.'; return }
$campaign = Get-Content -LiteralPath $campaignPath -Raw | ConvertFrom-Json
try {
    $workerJson = & docker inspect backend-benchmark-research --format '{{json .State}}' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Docker worker state is unavailable.' }
    $worker = $workerJson | ConvertFrom-Json
    if ($worker.Running) { Write-Output 'Worker: RUNNING' }
    else {
        Write-Output "Worker: STOPPED (exit $($worker.ExitCode), finished $($worker.FinishedAt))"
        if ($campaign.state -notin @('COMPLETE', 'NEEDS_ATTENTION')) {
            Write-Output 'Saved campaign state is stale; collection is not currently running. Resume with scripts/research.ps1.'
        }
    }
} catch { Write-Output 'Worker: UNKNOWN (Docker could not be inspected); the checkpoints below are saved state.' }
$campaign | Select-Object state,current_profile,started_at,completed_at,error | Format-List
foreach ($session in $campaign.sessions.PSObject.Properties) {
    $runtimePath = Join-Path $workspace ('results/raw/' + $session.Value + '/runtime.json')
    if (Test-Path -LiteralPath $runtimePath) {
        Write-Output 'Last saved worker phase (check its timestamp and the worker state above):'
        Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json | Format-List
    }
    $progressPath = Join-Path $workspace ('results/raw/' + $session.Value + '/progress.json')
    if (Test-Path -LiteralPath $progressPath) { Get-Content -LiteralPath $progressPath -Raw | ConvertFrom-Json | Format-List }
}

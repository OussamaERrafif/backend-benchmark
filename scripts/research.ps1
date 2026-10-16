# Start/resume the complete standard research campaign as a durable Docker job.
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Push-Location $workspace
try {
    $campaignPath = Join-Path $workspace 'verification/research-campaign.json'
    if (Test-Path -LiteralPath $campaignPath) {
        $campaign = Get-Content -LiteralPath $campaignPath -Raw | ConvertFrom-Json
        if ($campaign.state -eq 'COMPLETE') { Write-Output 'Full research is complete: reports/benchmark-report.html'; return }
    }
    $computer = Get-CimInstance Win32_ComputerSystem
    $processor = Get-CimInstance Win32_Processor
    # Windows PowerShell treats git's stderr as terminating under 'Stop'; a checkout without commits is fine.
    $commit = $null
    try { $commit = & git rev-parse HEAD 2>$null; if ($LASTEXITCODE -ne 0) { $commit = $null } } catch { $commit = $null }
    @{ os = [Environment]::OSVersion.VersionString; architecture = $env:PROCESSOR_ARCHITECTURE; cpu_model = @($processor.Name); logical_cpus = $computer.NumberOfLogicalProcessors; memory_gb = $computer.TotalPhysicalMemory / 1GB; benchmark_git_commit = $commit; date = [DateTime]::UtcNow.ToString('o') } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath verification/host-native.json -Encoding utf8
    $awakePath = Join-Path $workspace 'verification/research-awake.json'
    $awakeScript = Join-Path $PSScriptRoot 'research-awake.ps1'
    $awakeRunning = $false
    if (Test-Path -LiteralPath $awakePath) {
        $awake = Get-Content -LiteralPath $awakePath -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($awake.pid)" -ErrorAction SilentlyContinue
        $awakeRunning = $null -ne $process -and $process.CommandLine.Contains($awakeScript)
    }
    if (-not $awakeRunning) {
        $shellPath = (Get-Process -Id $PID).Path
        $helper = Start-Process -FilePath $shellPath -ArgumentList @('-NoProfile','-NonInteractive','-File',('"' + $awakeScript + '"')) -WindowStyle Hidden -PassThru
        @{ pid=$helper.Id; script=$awakeScript; started_at=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $awakePath -Encoding utf8
    }
    # Acquire the process-held sleep request before starting collection.
    $powerStatePath = Join-Path $workspace 'verification/research-power-request.json'
    $powerReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-Path -LiteralPath $powerStatePath) {
            try {
                $powerState = Get-Content -LiteralPath $powerStatePath -Raw | ConvertFrom-Json
                $awake = Get-Content -LiteralPath $awakePath -Raw | ConvertFrom-Json
                $powerReady = $powerState.active -and $powerState.pid -eq $awake.pid
            } catch { }
        }
        if ($powerReady) { break }
        Start-Sleep -Seconds 1
    }
    if (-not $powerReady) { throw 'Sleep-prevention helper did not acquire its power request; collection was not started.' }
    & docker compose up -d --no-build research
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the research worker.' }
    & docker update --restart unless-stopped backend-benchmark-research
    if ($LASTEXITCODE -ne 0) { throw 'Could not restore the research worker restart policy.' }
    Write-Output 'Full research worker started. It checkpoints measurements and updates the report automatically.'
    Write-Output 'Read progress with scripts/research-status.ps1 or docker logs --tail 30 backend-benchmark-research.'
    Write-Output 'A temporary Windows request prevents idle system sleep while the worker is active.'
} finally { Pop-Location }

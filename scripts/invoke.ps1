param([Parameter(Mandatory=$true)][string]$Action, [string[]]$ActionArguments = @())
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Push-Location $workspace
try {
    & docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon unavailable. Start Docker Desktop with Linux containers.' }
    & docker compose version
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required.' }
    $availableBytes = (Get-PSDrive -Name ([IO.Path]::GetPathRoot($workspace).Substring(0,1))).Free
    if ($availableBytes -lt 5GB) { throw 'At least 5 GB disk space is required; 20+ GB recommended.' }
    $portUsers = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    if ($portUsers -and -not (& docker compose ps --status running -q)) { throw 'Host port 8080 is already occupied.' }
    New-Item -ItemType Directory -Path verification -Force | Out-Null
    $computer = Get-CimInstance Win32_ComputerSystem
    $processor = Get-CimInstance Win32_Processor
    $commit = & git rev-parse HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { $commit = $null }
    @{ os = [Environment]::OSVersion.VersionString; architecture = $env:PROCESSOR_ARCHITECTURE; cpu_model = @($processor.Name); logical_cpus = $computer.NumberOfLogicalProcessors; memory_gb = $computer.TotalPhysicalMemory / 1GB; free_disk_gb = $availableBytes / 1GB; benchmark_git_commit = $commit; date = [DateTime]::UtcNow.ToString('o') } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath verification/host-native.json -Encoding utf8
    & docker compose build runner
    if ($LASTEXITCODE -ne 0) { throw 'Benchmark tooling image failed to build.' }
    $environmentArgs = @()
    $names = 'BENCH_MODE','REPETITIONS','WARMUP_DURATION','BENCH_DURATION','RPS_STEPS','SCENARIOS','MAX_ERROR_RATE','MAX_P95_MS','MAX_P99_MS','MIN_TARGET_ACHIEVEMENT','DB_POOL_SIZE','RANDOM_SEED','COOLDOWN_SECONDS','STRESS_DURATION','RUN_STRESS','PREALLOCATED_VUS','MAX_VUS','POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD'
    foreach ($name in $names) { if (Test-Path -LiteralPath "Env:$name") { $environmentArgs += @('-e',$name) } }
    if ($Action -eq 'build') {
        $buildLock = Join-Path $workspace '.benchmark-lock'
        New-Item -ItemType Directory -Path $buildLock -ErrorAction Stop | Out-Null
        try {
            & docker compose run --rm --no-deps @environmentArgs runner doctor
            if ($LASTEXITCODE -ne 0) { throw 'Environment doctor failed.' }
            # Host Docker retains Desktop proxy, registry credentials and buildx setup.
            & docker compose --profile '*' build
            if ($LASTEXITCODE -ne 0) { throw 'One or more images failed to build.' }
        } finally { Remove-Item -LiteralPath $buildLock -ErrorAction Stop }
        return
    }
    & docker compose run --rm --no-deps @environmentArgs runner $Action @ActionArguments
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See preserved raw results and logs." }
} finally { Pop-Location }

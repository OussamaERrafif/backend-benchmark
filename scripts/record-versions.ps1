$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Push-Location $workspace
try {
    $observed = @{}
    $observed.java = (& docker run --rm --entrypoint java backend-benchmark-springboot:1.0.0 -version 2>&1 | Out-String).Trim()
    $observed.python = (& docker run --rm --entrypoint python backend-benchmark-fastapi:1.0.0 --version | Out-String).Trim()
    $observed.php = (& docker run --rm --entrypoint php backend-benchmark-laravel:1.0.0 --version | Out-String).Trim()
    $observed.node = (& docker run --rm --entrypoint node backend-benchmark-express:1.0.0 --version | Out-String).Trim()
    $observed.dotnet = (& docker run --rm --entrypoint dotnet backend-benchmark-aspnet:1.0.0 --list-runtimes | Out-String).Trim()
    $observed.go_compiler = (& docker run --rm golang:1.24.1-bookworm go version | Out-String).Trim()
    $observed.rust_compiler = (& docker run --rm rust:1.85.1-bookworm rustc --version | Out-String).Trim()
    $observed.k6 = (& docker run --rm grafana/k6:0.57.0 version | Out-String).Trim()
    $observed.postgres = (& docker run --rm postgres:17.4-bookworm postgres --version | Out-String).Trim()
    $observed | ConvertTo-Json | Set-Content -LiteralPath verification/observed-versions.json -Encoding utf8
} finally { Pop-Location }

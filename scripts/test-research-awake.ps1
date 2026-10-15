# Exercise real Windows request acquisition, polling-gap refresh and release.
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$temporaryRoot = Join-Path $workspace 'tmp'
$testRoot = Join-Path $temporaryRoot ('power-request-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $testRoot 'scripts'),(Join-Path $testRoot 'verification') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'research-awake.ps1') -Destination (Join-Path $testRoot 'scripts/research-awake.ps1')
$testCampaign = Join-Path $testRoot 'verification/research-campaign.json'
@{state='RUNNING'} | ConvertTo-Json | Set-Content -LiteralPath $testCampaign
$script:polls = 0
function docker {
    $script:polls++
    if ($script:polls -eq 1) { Start-Sleep -Seconds 8 }
    if ($script:polls -eq 2) { @{state='VALIDATING'} | ConvertTo-Json | Set-Content -LiteralPath $testCampaign }
    if ($script:polls -eq 3) { @{state='COMPLETE'} | ConvertTo-Json | Set-Content -LiteralPath $testCampaign }
    $global:LASTEXITCODE = 0
    'true'
}
try {
    & (Join-Path $testRoot 'scripts/research-awake.ps1') -StartupGraceSeconds 30 -PollSeconds 1
    $state = Get-Content -LiteralPath (Join-Path $testRoot 'verification/research-power-request.json') -Raw | ConvertFrom-Json
    if ($state.active -or -not $state.released_at) { throw 'Request was not released on completion.' }
    if ($state.resume_refresh_count -lt 1) { throw 'Polling gap did not reacquire the request.' }
    if (-not $state.idle_timer_reset_at) { throw 'System idle timer was not refreshed.' }
    if ($state.campaign_state -ne 'VALIDATING') { throw 'Request was released before final validation completed.' }
    @{verified_at=[DateTime]::UtcNow.ToString('o'); result='PASS'; checks=@('acquisition','polling-gap reacquisition','idle timer refresh','retention during final validation','completion release'); helper_state=$state} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $workspace 'verification/research-power-request-test.json') -Encoding utf8
    'Windows power-request lifecycle PASS'
} finally {
    $resolvedTestRoot = (Resolve-Path -LiteralPath $testRoot).Path
    $resolvedTemporaryRoot = (Resolve-Path -LiteralPath $temporaryRoot).Path.TrimEnd('\') + '\'
    if (-not $resolvedTestRoot.StartsWith($resolvedTemporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Test cleanup escaped the workspace temporary directory.' }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}

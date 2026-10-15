# Hold a temporary Windows sleep-prevention request only while research runs.
param(
    [ValidateRange(1, 3600)][int]$StartupGraceSeconds = 300,
    [ValidateRange(1, 60)][int]$PollSeconds = 15
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$campaignPath = Join-Path $workspace 'verification/research-campaign.json'
$powerStatePath = Join-Path $workspace 'verification/research-power-request.json'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class BenchmarkPowerRequest {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct ReasonContext {
        public uint Version;
        public uint Flags;
        [MarshalAs(UnmanagedType.LPWStr)] public string SimpleReasonString;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr PowerCreateRequest(ref ReasonContext context);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PowerSetRequest(IntPtr handle, int requestType);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PowerClearRequest(IntPtr handle, int requestType);
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint flags);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CloseHandle(IntPtr handle);
}
'@
$reason = New-Object BenchmarkPowerRequest+ReasonContext
$reason.Version = 0
$reason.Flags = 1
$reason.SimpleReasonString = 'Backend benchmark experiment is collecting measurements'
$powerHandle = [BenchmarkPowerRequest]::PowerCreateRequest([ref]$reason)
if ($powerHandle -eq [IntPtr]::Zero -or $powerHandle -eq [IntPtr](-1)) {
    throw "Could not create sleep-prevention request: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
}
$requestSet = $false
$started = [DateTime]::UtcNow
$lastRunning = $started
$seenRunning = $false
$lastPoll = $started
$powerState = @{ pid=$PID; script=$PSCommandPath; started_at=$started.ToString('o'); active=$false; api='PowerSetRequest'; request='SystemRequired'; resume_refresh_count=0 }
Push-Location $workspace
try {
    if (-not [BenchmarkPowerRequest]::PowerSetRequest($powerHandle, 1)) {
        throw "Windows sleep-prevention request failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    $requestSet = $true
    $powerState.active = $true
    $powerState.request_acquired_at = [DateTime]::UtcNow.ToString('o')
    $powerState | ConvertTo-Json | Set-Content -LiteralPath $powerStatePath -Encoding utf8
    while ($true) {
        $now = [DateTime]::UtcNow
        $pollGap = ($now - $lastPoll).TotalSeconds
        $lastPoll = $now
        if ($pollGap -gt ($PollSeconds + 5)) {
            # Windows terminates power requests on explicit sleep. A live helper
            # and a saved active=true flag do not prove the old request survived.
            # Acquire a fresh handle before releasing the old one after a gap.
            $replacement = [BenchmarkPowerRequest]::PowerCreateRequest([ref]$reason)
            if ($replacement -eq [IntPtr]::Zero -or $replacement -eq [IntPtr](-1)) {
                throw 'Could not recreate the sleep-prevention request after a polling gap.'
            }
            if (-not [BenchmarkPowerRequest]::PowerSetRequest($replacement, 1)) {
                [BenchmarkPowerRequest]::CloseHandle($replacement) | Out-Null
                throw 'Could not reacquire the sleep-prevention request after a polling gap.'
            }
            [BenchmarkPowerRequest]::PowerClearRequest($powerHandle, 1) | Out-Null
            [BenchmarkPowerRequest]::CloseHandle($powerHandle) | Out-Null
            $powerHandle = $replacement
            $powerState.resume_refresh_count++
            $powerState.last_poll_gap_seconds = $pollGap
            $powerState.request_acquired_at = $now.ToString('o')
        }
        # A non-continuous SystemRequired call resets only the system idle timer.
        # Refresh it on every poll; explicit sleep and critical battery actions
        # remain available, and the display may still turn off normally.
        if ([BenchmarkPowerRequest]::SetThreadExecutionState(1) -eq 0) {
            throw 'Could not refresh the Windows system idle timer.'
        }
        $powerState.idle_timer_reset_at = $now.ToString('o')
        $campaign = $null
        try {
            $campaign = Get-Content -LiteralPath $campaignPath -Raw | ConvertFrom-Json
            $running = & docker inspect --format '{{.State.Running}}' backend-benchmark-research 2>$null
            if ($LASTEXITCODE -eq 0 -and $running -eq 'true') {
                $lastRunning = [DateTime]::UtcNow
                if ($campaign.state -in @('RUNNING','INITIALIZING','VALIDATING')) { $seenRunning = $true }
            }
        } catch { }
        # On launch the saved state can still say NEEDS_ATTENTION while Docker
        # initializes the resumed worker. Do not release the request in that race.
        if ($campaign -and $campaign.state -notin @('RUNNING','INITIALIZING','VALIDATING') -and
            ($seenRunning -or ([DateTime]::UtcNow - $started).TotalSeconds -ge $StartupGraceSeconds)) { break }
        # Allow a Docker restart; do not hold the machine awake indefinitely
        # after a stopped worker whose last checkpoint still says RUNNING.
        if (([DateTime]::UtcNow - $lastRunning).TotalMinutes -ge 5) { break }
        $powerState.checked_at = [DateTime]::UtcNow.ToString('o')
        $powerState.campaign_state = if ($campaign) { $campaign.state } else { 'UNAVAILABLE' }
        $powerState | ConvertTo-Json | Set-Content -LiteralPath $powerStatePath -Encoding utf8
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    if ($requestSet) { [BenchmarkPowerRequest]::PowerClearRequest($powerHandle, 1) | Out-Null }
    [BenchmarkPowerRequest]::CloseHandle($powerHandle) | Out-Null
    $powerState.active = $false
    $powerState.released_at = [DateTime]::UtcNow.ToString('o')
    $powerState | ConvertTo-Json | Set-Content -LiteralPath $powerStatePath -Encoding utf8
    Pop-Location
}

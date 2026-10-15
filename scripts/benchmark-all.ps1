param([string]$Profile = 'small')
& "$PSScriptRoot/invoke.ps1" benchmark-all @($Profile)

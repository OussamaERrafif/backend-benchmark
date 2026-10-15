param([string]$Profile = 'small')
& "$PSScriptRoot/invoke.ps1" smoke-test @($Profile)

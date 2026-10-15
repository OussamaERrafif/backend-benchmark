param([Parameter(Mandatory=$true)][string]$Framework, [string]$Profile = 'small')
& "$PSScriptRoot/invoke.ps1" benchmark @($Framework,$Profile)

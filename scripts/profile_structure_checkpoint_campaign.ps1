[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Binary,
    [string]$SourceCommit,
    [string]$PythonExe = 'python',
    [string]$CurrentRuntime = 'D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18-v3'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
if ($env:COMPUTERNAME -ne 'DESKTOP-SAAI85T') { throw 'Run this command on the workstation.' }
$python = (Get-Command $PythonExe -ErrorAction Stop).Source
& $python -c 'import rich, psutil, requests, dotenv'
if ($LASTEXITCODE -ne 0) { throw 'Activate ml4t first. No campaign stop has been requested.' }
$profileArgs = @((Join-Path $PSScriptRoot 'profile_structure_checkpoint_campaign.py'), '--binary', $Binary, '--current-runtime', $CurrentRuntime)
if ($SourceCommit) { $profileArgs += @('--source-commit', $SourceCommit) }
& $python @profileArgs
exit $LASTEXITCODE

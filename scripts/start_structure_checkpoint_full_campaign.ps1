[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [switch]$PreflightOnly
)

# Full reconstruction has a new immutable identity. Existing August campaigns
# and their checkpoints are retained; this does not resume their frozen plans.
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
if ($env:COMPUTERNAME -ne 'DESKTOP-SAAI85T') { throw 'Run this script on DESKTOP-SAAI85T.' }
$parameters = @{
    CheckpointSetId = 'canonical-tradable-20250101-20260904-prominence-v18-v1'
    RuntimeDir = 'D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18-20260904-v1'
    StartDate = '2025-01-01'
    EndDate = '2026-09-04'
    Workers = 96
    PriorityRanking = 'D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18-v3\priority-ranking.json'
    PythonExe = $PythonExe
}
if ($PreflightOnly) { $parameters.CampaignArguments = @('--preflight-only') }
& (Join-Path $PSScriptRoot 'run_structure_checkpoint_campaign.ps1') @parameters
exit $LASTEXITCODE

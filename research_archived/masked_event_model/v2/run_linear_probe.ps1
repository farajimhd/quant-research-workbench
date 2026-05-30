$ErrorActionPreference = 'Stop'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
  $global:PSNativeCommandUseErrorActionPreference = $false
}
$env:PYTHONUNBUFFERED = '1'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeCandidate = Join-Path $scriptDir 'research\masked_event_model\v2\train_linear_probe.py'
if (Test-Path $runtimeCandidate) {
  $runtimeRoot = $scriptDir
} else {
  $runtimeRoot = (Resolve-Path (Join-Path $scriptDir '..\..\..')).Path
}
$repoEnv = 'D:\TradingCodes\quant-research-workbench\.env'
$runtimeEnv = Join-Path $runtimeRoot '.env'
$env:PYTHONPATH = $runtimeRoot + [System.IO.Path]::PathSeparator + $env:PYTHONPATH
$env:DOTENV_PATHS = $repoEnv + [System.IO.Path]::PathSeparator + $runtimeEnv + [System.IO.Path]::PathSeparator + $env:DOTENV_PATHS
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logDir = 'D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v2\workstation_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ('masked_event_v2_linear_probe_' + $runStamp + '.log')
$wandbMode = if ($env:MASKED_EVENT_WANDB_MODE) { $env:MASKED_EVENT_WANDB_MODE } else { 'online' }
$wandbTimeout = if ($env:MASKED_EVENT_WANDB_INIT_TIMEOUT) { $env:MASKED_EVENT_WANDB_INIT_TIMEOUT } else { '120' }
Write-Host 'Starting masked event v2 linear probe at' (Get-Date -Format o)
Write-Host 'Runtime root:' $runtimeRoot
Write-Host 'Log:' $logPath
Write-Host 'W&B mode:' $wandbMode
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& "c:\Users\Mehdi\miniconda3\envs\ml4t\python.exe" -u (Join-Path $runtimeRoot 'research\masked_event_model\v2\train_linear_probe.py') `
  --output-root "D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v2" `
  --pretrain-run-name "mem-v2-d256-e2-t8-d4-mask70-chunk500-b256-nov2025" `
  --cache-root "D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v2" `
  --canonical-root "D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_v2" `
  --batch-size 256 `
  --train-steps 200 `
  --train-windows 20000 `
  --val-windows 20000 `
  --hidden-dim 0 `
  --learning-rate 1e-3 `
  --num-workers 0 `
  --device cuda `
  --seed 17 `
  --wandb-entity "mehdifaraji" `
  --wandb-project "May2026-masked-event-modeling" `
  --wandb-mode $wandbMode `
  --wandb-init-timeout $wandbTimeout `
  2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $logPath
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $oldErrorActionPreference
if ($exitCode -ne 0) { throw "Command failed with exit code $exitCode" }

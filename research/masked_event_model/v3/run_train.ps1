$ErrorActionPreference = 'Stop'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
  $global:PSNativeCommandUseErrorActionPreference = $false
}
$env:PYTHONUNBUFFERED = '1'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeCandidate = Join-Path $scriptDir 'research\masked_event_model\v3\train.py'
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
$mlRoot = if ($env:QW_MLOPS_ROOT) { $env:QW_MLOPS_ROOT } else { 'D:\TradingML' }
$logDir = Join-Path $mlRoot 'runtimes\masked_event_model\v3\launcher_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ('masked_event_v3_train_' + $runStamp + '.log')
$wandbMode = if ($env:MASKED_EVENT_WANDB_MODE) { $env:MASKED_EVENT_WANDB_MODE } else { 'online' }
$wandbTimeout = if ($env:MASKED_EVENT_WANDB_INIT_TIMEOUT) { $env:MASKED_EVENT_WANDB_INIT_TIMEOUT } else { '120' }
Write-Host 'Starting masked event v3 training at' (Get-Date -Format o)
Write-Host 'Runtime root:' $runtimeRoot
Write-Host 'Log:' $logPath
Write-Host 'W&B mode:' $wandbMode
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& "c:\Users\Mehdi\miniconda3\envs\ml4t\python.exe" -u (Join-Path $runtimeRoot 'research\masked_event_model\v3\train.py') `
  --cache-root "D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v2" `
  --canonical-root "D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_v2" `
  --train-start-date "2025-11-01" `
  --train-end-date "2025-11-30" `
  --validation-start-date "2025-12-01" `
  --validation-end-date "2025-12-05" `
  --test-start-date "2025-12-08" `
  --test-end-date "2025-12-12" `
  --tickers "ALL" `
  --context-seconds 30 `
  --chunk-ms 500 `
  --row-block-size 8192 `
  --loader-progress-windows 256 `
  --batch-size 256 `
  --epochs 3 `
  --num-workers 0 `
  --prefetch-factor 1 `
  --device cuda `
  --d-model 384 `
  --embedding-dim 256 `
  --n-heads 6 `
  --quote-event-layers 2 `
  --trade-event-layers 2 `
  --temporal-layers 8 `
  --decoder-layers 4 `
  --ffn-mult 4 `
  --encoder-visible-ratio 0.30 `
  --mask-ratio 0.70 `
  --learning-rate 2e-4 `
  --weight-decay 1e-4 `
  --scheduler "cosine_warm_restarts" `
  --scheduler-t0-steps 1000 `
  --scheduler-t-mult 2 `
  --scheduler-eta-min 1e-6 `
  --logging-steps 1 `
  --detailed-metrics-steps 10 `
  --profile-training-every-steps 10 `
  --profile-inference-every-steps 10 `
  --pretrain-validation-frequency 50 `
  --pretrain-validation-steps 4 `
  --checkpoint-steps 1000 `
  --checkpoint-latest-steps 10 `
  --checkpoint-archive-steps 5000 `
  --loader-prefetch-batches 1 `
  --wandb-entity "mehdifaraji" `
  --wandb-project "May2026-masked-event-modeling" `
  --wandb-run-name "mem-v3-d384-emb256-e2-t8-d4-mask70-chunk500-b256-nov2025" `
  --wandb-mode $wandbMode `
  --wandb-init-timeout $wandbTimeout `
  2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $logPath
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $oldErrorActionPreference
if ($exitCode -ne 0) { throw "Command failed with exit code $exitCode" }

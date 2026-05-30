$ErrorActionPreference = 'Stop'
$env:PYTHONUNBUFFERED = '1'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeCandidate = Join-Path $scriptDir 'research\masked_event_model\v1\train.py'
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
$logDir = 'D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v1\workstation_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ('masked_event_v1_train_' + $runStamp + '.log')
Write-Host 'Starting masked event v1 training at' (Get-Date -Format o)
Write-Host 'Runtime root:' $runtimeRoot
Write-Host 'Log:' $logPath
& "c:\Users\Mehdi\miniconda3\envs\ml4t\python.exe" -u (Join-Path $runtimeRoot 'research\masked_event_model\v1\train.py') `
  --cache-root "D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v2" `
  --canonical-root "D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_v2" `
  --output-root "D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v1" `
  --train-start-date "2025-11-01" `
  --train-end-date "2025-11-30" `
  --validation-start-date "2025-12-01" `
  --validation-end-date "2025-12-05" `
  --test-start-date "2025-12-08" `
  --test-end-date "2025-12-12" `
  --tickers "ALL" `
  --context-seconds 30 `
  --chunk-ms 500 `
  --batch-size 256 `
  --epochs 3 `
  --num-workers 8 `
  --prefetch-factor 4 `
  --device cuda `
  --d-model 512 `
  --n-heads 8 `
  --quote-event-layers 2 `
  --trade-event-layers 2 `
  --temporal-layers 8 `
  --decoder-layers 4 `
  --ffn-mult 4 `
  --mask-ratio 0.70 `
  --logging-steps 50 `
  --checkpoint-steps 1000 `
  --probe-every-steps 5000 `
  --probe-train-steps 200 `
  --probe-train-windows 20000 `
  --probe-val-windows 20000 `
  --wandb-entity "mehdifaraji" `
  --wandb-project "May2026-masked-event-modeling" `
  --wandb-run-name "mem-v1-d512-e2-t8-d4-mask70-chunk500-nov2025" `
  2>&1 | Tee-Object -FilePath $logPath
if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }

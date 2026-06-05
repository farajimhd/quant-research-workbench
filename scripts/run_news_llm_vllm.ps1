param(
    [string]$ModelKey = "openai-gpt-oss-20b",
    [string]$ModelRoot = "D:\models_artifacts\opensource",
    [string]$Manifest = "",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [string]$ServedModelName = "",
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Manifest.Trim()) {
    $Manifest = Join-Path $repoRoot "services\news-intelligence\models\opensource_models.json"
}

$manifestJson = Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json
$model = $manifestJson.models | Where-Object { $_.key -eq $ModelKey } | Select-Object -First 1
if (-not $model) {
    throw "Model key '$ModelKey' was not found in $Manifest"
}

$localPath = Join-Path $ModelRoot $ModelKey
$modelArg = if (Test-Path -LiteralPath $localPath) { $localPath } else { $model.repo_id }
if (-not $ServedModelName.Trim()) {
    if ($model.serving -and $model.serving.served_model_name) {
        $ServedModelName = [string]$model.serving.served_model_name
    } else {
        $ServedModelName = [string]$model.repo_id
    }
}

$vllm = Get-Command vllm -ErrorAction SilentlyContinue
if (-not $vllm) {
    throw "vLLM CLI was not found. Install vLLM in the model-serving environment before running this script."
}

$args = @(
    "serve",
    $modelArg,
    "--host", $HostName,
    "--port", [string]$Port,
    "--served-model-name", $ServedModelName
) + $ExtraArgs

Write-Host "Starting vLLM:"
Write-Host "  model key: $ModelKey"
Write-Host "  model arg: $modelArg"
Write-Host "  served model name: $ServedModelName"
Write-Host "  endpoint: http://$HostName`:$Port/v1"

& $vllm.Source @args

param([string]$Bind = "127.0.0.1:8802", [switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$env:MODEL_GATEWAY_BIND = $Bind
if ($CheckOnly) {
    python -c "from services.model_gateway.config import GatewayConfig; c=GatewayConfig.from_env(); print(c.bind, sorted(c.routes))"
    exit $LASTEXITCODE
}
python -m services.model_gateway.main

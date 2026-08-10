param([string]$Bind = "127.0.0.1:8803", [switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$env:MARKET_AI_BIND = $Bind
if ($CheckOnly) {
    python -c "import sys; sys.path.insert(0, r'services\\market-ai\\src'); from market_ai.contextual import HYPOTHESIS_SCHEMA; print('$Bind', len(HYPOTHESIS_SCHEMA['required']))"
    exit $LASTEXITCODE
}
python services\market-ai\run_service.py

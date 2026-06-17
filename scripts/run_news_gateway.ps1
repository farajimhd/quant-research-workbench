param(
    [string]$Bind = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if ($Bind.Trim()) {
    $env:NEWS_GATEWAY_BIND = $Bind.Trim()
}

if ($CheckOnly) {
    python -m services.news_gateway.main --check-only
    exit $LASTEXITCODE
}

python -m services.news_gateway.main

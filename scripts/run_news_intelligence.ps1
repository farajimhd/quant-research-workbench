param(
    [string]$Bind = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceDir = Join-Path $repoRoot "services\news-intelligence"

if ($Bind.Trim()) {
    $env:NEWS_INTELLIGENCE_BIND = $Bind.Trim()
}

if ($CheckOnly) {
    python -m compileall (Join-Path $serviceDir "news_intelligence") (Join-Path $serviceDir "scripts")
    exit $LASTEXITCODE
}

Push-Location $serviceDir
try {
    python -m news_intelligence.main
}
finally {
    Pop-Location
}

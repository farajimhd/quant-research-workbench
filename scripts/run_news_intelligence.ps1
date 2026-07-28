param(
    [string]$Bind = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceDir = Join-Path $repoRoot "services\news-intelligence"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$repoRoot;$env:PYTHONPATH" } else { $repoRoot }

if ($Bind.Trim()) {
    $env:NEWS_INTELLIGENCE_BIND = $Bind.Trim()
}

if ($CheckOnly) {
    python -c "import ast,pathlib; root=pathlib.Path(r'$serviceDir'); files=list((root/'news_intelligence').glob('*.py'))+list((root/'scripts').glob('*.py')); [ast.parse(p.read_text(encoding='utf-8')) for p in files]; print(f'AST OK {len(files)} files')"
    exit $LASTEXITCODE
}

Push-Location $serviceDir
try {
    python -m news_intelligence.main
}
finally {
    Pop-Location
}

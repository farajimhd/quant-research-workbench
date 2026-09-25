param(
    [Parameter(Mandatory = $true)][string]$BuildId,
    [Parameter(Mandatory = $true)][string]$Date,
    [ValidateRange(1, 16)][int]$Workers = 16
)

$ErrorActionPreference = 'Stop'
$repo = 'D:\TradingML\codes\quant-research-workbench-certificate-d8b09dff'
$runtime = 'D:\TradingML\runtimes\eligible-price-v1'
$python = 'C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe'
$script = Join-Path $repo 'scripts\clickhouse\build_liquidity_execution_prices.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf) -or
    -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Verified workstation checkout or Python executable is missing.'
}
if ($BuildId -notmatch '^[0-9a-f]{64}(-[0-9a-f]{12})?$' -or
    $Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw 'Build ID or session date is invalid.'
}
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$identity = (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$stdout = Join-Path $runtime "$identity.stdout.log"
$stderr = Join-Path $runtime "$identity.stderr.log"
$env:PYTHONDONTWRITEBYTECODE = '1'
$arguments = @(
    '-B', $script, '--build-id', $BuildId, '--date', $Date,
    '--workers', [string]$Workers, '--apply',
    '--confirm-eligible-price-publication'
)
$process = Start-Process -FilePath $python -ArgumentList $arguments `
    -WorkingDirectory $repo -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
Write-Output "Started eligible-price campaign PID $($process.Id)"
Write-Output "Progress: $stdout"
Write-Output "Errors:   $stderr"

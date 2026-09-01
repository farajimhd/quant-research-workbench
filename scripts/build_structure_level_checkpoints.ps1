[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]{1,32}$')]
    [string[]]$Ticker,
    [string[]]$TickerFile,
    [Parameter(Mandatory)]
    [datetime]$StartDate,
    [Parameter(Mandatory)]
    [datetime]$EndDate,
    [datetime]$RebuildStart,
    [string]$QmdLiveUrl = 'http://127.0.0.1:8795',
    [string]$RuntimeRoot = 'D:\TradingML\runtimes\qmd_gateway',
    [ValidateRange(1, 250000000)]
    [int]$EventLimit = 50000000,
    [ValidateRange(60, 7200)]
    [int]$TimeoutSeconds = 1800,
    [ValidateRange(1, 32)]
    [int]$Workers = 4,
    [ValidateRange(0, 31)]
    [int]$BootstrapDays = 14,
    [ValidateRange(0, 10)]
    [int]$MaxRetries = 3,
    [ValidateRange(0.1, 60)]
    [double]$RetryDelaySeconds = 2.0,
    [string]$ReportPath,
    [switch]$ContinueOnError,
    [switch]$Quiet,
    [string]$PythonExe = 'C:\Users\g835l\miniconda3\envs\ml4t\python.exe'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$tokenPath = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) 'operator_token.dpapi'
if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
    throw "QMD operator token is unavailable: $tokenPath. Start QMD Live with the managed launcher."
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable is unavailable: $PythonExe"
}
if ((-not $Ticker -or $Ticker.Count -eq 0) -and (-not $TickerFile -or $TickerFile.Count -eq 0)) {
    throw 'Provide -Ticker or -TickerFile.'
}
foreach ($path in $TickerFile) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Ticker universe file is unavailable: $path"
    }
}

Add-Type -AssemblyName System.Security
$protectedTokenBytes = [Convert]::FromBase64String(
    [IO.File]::ReadAllText($tokenPath, [Text.Encoding]::ASCII)
)
$operatorTokenBytes = [Security.Cryptography.ProtectedData]::Unprotect(
    $protectedTokenBytes,
    $null,
    [Security.Cryptography.DataProtectionScope]::LocalMachine
)
$env:QMD_OPERATOR_TOKEN = [Text.Encoding]::UTF8.GetString($operatorTokenBytes)
try {
    $arguments = @(
        'scripts\build_structure_level_checkpoints.py',
        '--start-date', $StartDate.ToString('yyyy-MM-dd'),
        '--end-date', $EndDate.ToString('yyyy-MM-dd'),
        '--qmd-url', $QmdLiveUrl,
        '--event-limit', [string]$EventLimit,
        '--timeout-seconds', [string]$TimeoutSeconds,
        '--workers', [string]$Workers,
        '--bootstrap-days', [string]$BootstrapDays,
        '--max-retries', [string]$MaxRetries,
        '--retry-delay-seconds', [string]$RetryDelaySeconds
    )
    foreach ($symbol in $Ticker) {
        $arguments += @('--ticker', $symbol.Trim().ToUpperInvariant())
    }
    foreach ($path in $TickerFile) {
        $arguments += @('--ticker-file', [IO.Path]::GetFullPath($path))
    }
    if ([string]::IsNullOrWhiteSpace($ReportPath)) {
        $ReportPath = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) 'structure-checkpoint-builder-status.json'
    }
    $arguments += @('--report-path', [IO.Path]::GetFullPath($ReportPath))
    if ($PSBoundParameters.ContainsKey('RebuildStart')) {
        $arguments += @('--rebuild-start', $RebuildStart.ToString('yyyy-MM-dd'))
    }
    if ($ContinueOnError) {
        $arguments += '--continue-on-error'
    }
    if ($Quiet) {
        $arguments += '--quiet'
    }
    & $PythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Structural Level Book checkpoint builder failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item Env:QMD_OPERATOR_TOKEN -ErrorAction SilentlyContinue
    if ($operatorTokenBytes) {
        [Array]::Clear($operatorTokenBytes, 0, $operatorTokenBytes.Length)
    }
}

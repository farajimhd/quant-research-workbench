[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9._-]{1,32}$')]
    [string]$Ticker,
    [datetime]$StartUtc = [datetime]::Parse('2019-01-01T00:00:00Z').ToUniversalTime(),
    [datetime]$AsOfUtc = [datetime]::UtcNow,
    [ValidateRange(1000, 250000000)]
    [int]$EventLimit = 50000000,
    [string]$QmdLiveUrl = 'http://127.0.0.1:8795',
    [string]$QmdHistoryUrl = 'http://127.0.0.1:8801',
    [string]$RuntimeRoot = 'D:\TradingML\runtimes\qmd_gateway',
    [ValidateRange(60, 7200)]
    [int]$TimeoutSeconds = 3600,
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$tickerValue = $Ticker.Trim().ToUpperInvariant()
$startValue = $StartUtc.ToUniversalTime().ToString('o')
$asOfValue = $AsOfUtc.ToUniversalTime().ToString('o')

Write-Host 'Generic Structure checkpoint recovery'
Write-Host ("  Ticker       {0}" -f $tickerValue)
Write-Host ("  Replay start {0}" -f $startValue)
Write-Host ("  As of        {0}" -f $asOfValue)
Write-Host ("  Event limit  {0:N0}" -f $EventLimit)

$planUri = '{0}/source-plan?start={1}&end={2}&tickers={3}' -f `
    $QmdHistoryUrl.TrimEnd('/'), `
    [uri]::EscapeDataString($startValue), `
    [uri]::EscapeDataString($asOfValue), `
    [uri]::EscapeDataString($tickerValue)
$plan = Invoke-RestMethod -Uri $planUri -TimeoutSec $TimeoutSeconds
$gaps = @($plan.segments | Where-Object { [string]$_.tier -eq 'gap' })
Write-Host ("[preflight] source plan {0}; segments={1}; gaps={2}" -f $plan.plan_hash, @($plan.segments).Count, $gaps.Count)
if ($gaps.Count -gt 0) {
    throw "Rebuild refused because the source plan contains $($gaps.Count) uncovered segment(s)."
}
if ($PlanOnly) {
    Write-Host '[complete] Plan is gap-free; no checkpoint was changed.'
    return
}

$tokenPath = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) 'operator_token.dpapi'
if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
    throw "QMD operator token is unavailable: $tokenPath. Start QMD Live with the managed launcher."
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
$operatorToken = [Text.Encoding]::UTF8.GetString($operatorTokenBytes)
$body = @{
    start = $startValue
    as_of = $asOfValue
    event_limit = $EventLimit
} | ConvertTo-Json
$uri = '{0}/admin/structure-checkpoints/{1}/rebuild' -f `
    $QmdLiveUrl.TrimEnd('/'), [uri]::EscapeDataString($tickerValue)

if (-not $PSCmdlet.ShouldProcess($tickerValue, 'Rebuild and replace the blocked Generic Structure checkpoint')) {
    return
}
Write-Host '[active] Replaying canonical history; the blocked record remains authoritative until persistence completes.'
$started = Get-Date
$result = Invoke-RestMethod `
    -Uri $uri `
    -Method Post `
    -Headers @{ 'X-QMD-Operator-Token' = $operatorToken } `
    -ContentType 'application/json' `
    -Body $body `
    -TimeoutSec $TimeoutSeconds
$elapsed = (Get-Date) - $started

Write-Host ("[complete] Rebuilt {0:N0} canonical events in {1:N1}s." -f $result.event_count, $elapsed.TotalSeconds)
Write-Host ("  Checkpoint   {0} sequence={1}" -f $result.checkpoint_updated_at, $result.checkpoint_arrival_sequence)
Write-Host ("  Source plan  {0}" -f $result.source_plan_hash)
Write-Host ("  Revision     {0}" -f $result.source_revision_token)
Write-Host ("  Registry     active; previous={0}" -f $result.previous_error_code)

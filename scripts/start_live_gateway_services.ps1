[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [string]$WindowsTerminalExe = "",
    [string]$TerminalWindowName = "quant-research-workbench-services",
    [ValidateNotNullOrEmpty()]
    [string]$IbkrAccount = "paper",
    [ValidateRange(0, 3600)]
    [int]$ReferenceDelaySeconds = 60,
    [ValidateNotNullOrEmpty()]
    [string]$IbkrSupervisorHealthUrl = "http://127.0.0.1:8800/health"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$newsLauncher = Join-Path $PSScriptRoot "run_news_gateway.ps1"
$secLauncher = Join-Path $PSScriptRoot "run_sec_gateway.ps1"
$referenceLauncher = Join-Path $PSScriptRoot "run_reference_gateway.ps1"
$ibkrLauncher = Join-Path $PSScriptRoot "run_ibkr_gateway_supervisor.ps1"

function Resolve-CondaEnvironmentPython {
    param([string]$EnvironmentName)

    $commonCandidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\envs\$EnvironmentName\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\envs\$EnvironmentName\python.exe")
    )
    foreach ($candidate in $commonCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
    if (-not $condaCommand) {
        return ""
    }
    try {
        $environmentInfo = conda info --envs --json | ConvertFrom-Json
        foreach ($environmentPath in $environmentInfo.envs) {
            if ((Split-Path -Leaf $environmentPath).Trim().ToLowerInvariant() -ne $EnvironmentName.Trim().ToLowerInvariant()) {
                continue
            }
            $candidate = Join-Path $environmentPath "python.exe"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return $candidate
            }
        }
    }
    catch {
        return ""
    }
    return ""
}

function Resolve-PythonExecutable {
    param(
        [string]$Requested,
        [string]$EnvironmentName
    )

    if ($Requested.Trim()) {
        $requestedCommand = Get-Command $Requested.Trim() -ErrorAction SilentlyContinue
        if ($requestedCommand) {
            return $requestedCommand.Source
        }
        if (Test-Path -LiteralPath $Requested.Trim() -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Requested.Trim()).Path
        }
        throw "The requested Python executable does not exist: $Requested"
    }

    if ($env:CONDA_PREFIX -and
        (Split-Path -Leaf $env:CONDA_PREFIX).Trim().ToLowerInvariant() -eq $EnvironmentName.Trim().ToLowerInvariant()) {
        $activeCondaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activeCondaPython -PathType Leaf) {
            return $activeCondaPython
        }
    }

    $environmentPython = Resolve-CondaEnvironmentPython -EnvironmentName $EnvironmentName
    if ($environmentPython) {
        return $environmentPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Python was not found. Activate the $EnvironmentName environment or pass -PythonExe <path-to-python.exe>."
}

function Resolve-WindowsTerminalExecutable {
    param([string]$Requested)

    if ($Requested.Trim()) {
        $requestedCommand = Get-Command $Requested.Trim() -ErrorAction SilentlyContinue
        if ($requestedCommand) {
            return $requestedCommand.Source
        }
        if (Test-Path -LiteralPath $Requested.Trim() -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Requested.Trim()).Path
        }
        throw "The requested Windows Terminal executable does not exist: $Requested"
    }

    $terminalCommand = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($terminalCommand) {
        return $terminalCommand.Source
    }

    $appExecutionAlias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\wt.exe"
    if (Test-Path -LiteralPath $appExecutionAlias -PathType Leaf) {
        return $appExecutionAlias
    }

    throw "Windows Terminal (wt.exe) was not found. Install Windows Terminal or pass -WindowsTerminalExe <path-to-wt.exe>."
}

function ConvertTo-PowerShellLiteral {
    param([string]$Value)

    return "'" + $Value.Replace("'", "''") + "'"
}

function Assert-Launcher {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required service launcher is missing: $Path"
    }
}

Assert-Launcher -Path $newsLauncher
Assert-Launcher -Path $secLauncher
Assert-Launcher -Path $referenceLauncher
Assert-Launcher -Path $ibkrLauncher

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe -EnvironmentName $CondaEnv
$resolvedWindowsTerminal = Resolve-WindowsTerminalExecutable -Requested $WindowsTerminalExe
$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$pythonLiteral = ConvertTo-PowerShellLiteral -Value $resolvedPython
$ibkrHealthUrlLiteral = ConvertTo-PowerShellLiteral -Value $IbkrSupervisorHealthUrl

$referenceCommandLines = @(
    "Write-Host 'Reference Gateway is waiting for the IBKR Gateway Supervisor to start.'",
    '$ibkrHealth = $null',
    'while ($null -eq $ibkrHealth) {',
    '    try {',
    "        `$ibkrHealth = Invoke-RestMethod -Uri $ibkrHealthUrlLiteral -TimeoutSec 5",
    '    }',
    '    catch {',
    '        Write-Host (''IBKR supervisor health is not reachable yet: {0}. Retrying in 5 seconds.'' -f $_.Exception.Message)',
    '        Start-Sleep -Seconds 5',
    '    }',
    '}',
    "Write-Host 'IBKR supervisor is reachable. Waiting $ReferenceDelaySeconds seconds before Reference preflight.'",
    "Start-Sleep -Seconds $ReferenceDelaySeconds",
    '$ibkrReady = $false',
    'while (-not $ibkrReady) {',
    '    try {',
    "        `$ibkrHealth = Invoke-RestMethod -Uri $ibkrHealthUrlLiteral -TimeoutSec 5",
    '        $gatewayStatus = [string]$ibkrHealth.metrics.gateway_status',
    '        $authStatus = [string]$ibkrHealth.metrics.auth_status',
    '        $ibkrReady = $gatewayStatus.Trim().ToLowerInvariant() -eq ''ready'' -and $authStatus.Trim().ToLowerInvariant() -eq ''authenticated''',
    '        if (-not $ibkrReady) {',
    '            Write-Host (''IBKR is not ready for Reference: gateway={0}, auth={1}. Retrying in 10 seconds.'' -f $gatewayStatus, $authStatus)',
    '            Start-Sleep -Seconds 10',
    '        }',
    '    }',
    '    catch {',
    '        Write-Host (''IBKR supervisor health check failed: {0}. Retrying in 10 seconds.'' -f $_.Exception.Message)',
    '        Start-Sleep -Seconds 10',
    '    }',
    '}',
    "Write-Host 'IBKR is ready and authenticated. Starting Reference Gateway.'",
    ("& " + (ConvertTo-PowerShellLiteral -Value $referenceLauncher) + " -PythonExe $pythonLiteral")
)
$referenceCommand = $referenceCommandLines -join [Environment]::NewLine

$serviceTabs = @(
    [pscustomobject]@{
        Title = "News Gateway"
        Command = "& " + (ConvertTo-PowerShellLiteral -Value $newsLauncher) +
            " -CondaEnv " + (ConvertTo-PowerShellLiteral -Value $CondaEnv) +
            " -PythonExe $pythonLiteral"
    },
    [pscustomobject]@{
        Title = "SEC Gateway"
        Command = "& " + (ConvertTo-PowerShellLiteral -Value $secLauncher) +
            " -CondaEnv " + (ConvertTo-PowerShellLiteral -Value $CondaEnv) +
            " -PythonExe $pythonLiteral"
    },
    [pscustomobject]@{
        Title = "Reference Gateway"
        Command = $referenceCommand
    },
    [pscustomobject]@{
        Title = "IBKR Gateway Supervisor"
        Command = "& " + (ConvertTo-PowerShellLiteral -Value $ibkrLauncher) +
            " -PythonExe $pythonLiteral" +
            " -Account " + (ConvertTo-PowerShellLiteral -Value $IbkrAccount)
    }
)

foreach ($serviceTab in $serviceTabs) {
    Write-Verbose ("Generated {0} command:`n{1}" -f $serviceTab.Title, $serviceTab.Command)
    $commandTokens = $null
    $commandErrors = $null
    [void][Management.Automation.Language.Parser]::ParseInput(
        $serviceTab.Command,
        [ref]$commandTokens,
        [ref]$commandErrors
    )
    if ($commandErrors.Count -gt 0) {
        $messages = @($commandErrors | ForEach-Object { $_.Message }) -join "; "
        throw "The generated '$($serviceTab.Title)' PowerShell command is invalid: $messages"
    }
}

function Open-ServiceTab {
    param(
        [string]$Title,
        [string]$Command
    )

    if (-not $PSCmdlet.ShouldProcess(
        "$TerminalWindowName / $Title",
        "Open an independent PowerShell tab in Windows Terminal"
    )) {
        return
    }

    & $resolvedWindowsTerminal `
        -w $TerminalWindowName `
        new-tab `
        --title $Title `
        -d $repoRoot `
        $powerShellExe `
        -NoLogo `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -Command $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Terminal failed to create the '$Title' tab (exit code $LASTEXITCODE)."
    }
    Start-Sleep -Milliseconds 150
}

foreach ($serviceTab in $serviceTabs) {
    Open-ServiceTab -Title $serviceTab.Title -Command $serviceTab.Command
}

if ($WhatIfPreference) {
    Write-Host ""
    Write-Host "Validation complete; no Windows Terminal tabs were opened."
    return
}

Write-Host ""
Write-Host "Opened four independent PowerShell tabs in Windows Terminal window '$TerminalWindowName', in this order:"
for ($index = 0; $index -lt $serviceTabs.Count; $index++) {
    Write-Host ("  {0}. {1}" -f ($index + 1), $serviceTabs[$index].Title)
}
Write-Host "This starter now exits instead of supervising the four launcher processes."
Write-Host "Reference waits for IBKR Supervisor health, then at least $ReferenceDelaySeconds seconds, then ready/authenticated state."
Write-Host "Stop all matching instances with scripts\stop_live_gateway_services.ps1."

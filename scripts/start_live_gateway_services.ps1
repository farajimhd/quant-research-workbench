[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$CondaEnv = "ml4t",
    [string]$PythonExe = "",
    [string]$WindowsTerminalExe = "",
    [ValidateSet("Auto", "Caller", "Named")]
    [string]$TerminalTarget = "Auto",
    [string]$TerminalWindowName = "quant-research-workbench-gateways",
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
$textIntelligenceLauncher = Join-Path $PSScriptRoot "run_text_intelligence.ps1"
$serviceTabHost = Join-Path $PSScriptRoot "run_windows_terminal_service_tab.ps1"
$terminalWindowTargetHelper = Join-Path $PSScriptRoot "windows_terminal_window_target.ps1"

if (-not (Test-Path -LiteralPath $terminalWindowTargetHelper -PathType Leaf)) {
    throw "Required Windows Terminal target helper is missing: $terminalWindowTargetHelper"
}
. $terminalWindowTargetHelper

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

function ConvertTo-PowerShellEncodedCommand {
    param([string]$Command)

    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
}

function New-InteractiveGatewayCommand {
    param(
        [string]$RichEnabledVariable,
        [string]$ScreenEnabledVariable,
        [string]$Command
    )

    return @(
        ('$env:' + $RichEnabledVariable + " = 'true'"),
        ('$env:' + $ScreenEnabledVariable + " = 'true'"),
        $Command
    ) -join [Environment]::NewLine
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
Assert-Launcher -Path $textIntelligenceLauncher
Assert-Launcher -Path $serviceTabHost

$resolvedPython = Resolve-PythonExecutable -Requested $PythonExe -EnvironmentName $CondaEnv
$callerTerminalWindow = Get-WindowsTerminalCallerWindow `
    -PythonExecutable $resolvedPython
$resolvedWindowsTerminal = Resolve-WindowsTerminalExecutable -Requested $WindowsTerminalExe
$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$terminalWindowTarget = Resolve-WindowsTerminalTarget `
    -Mode $TerminalTarget `
    -FallbackWindowName $TerminalWindowName `
    -CallerWindowHandle $callerTerminalWindow
if ($terminalWindowTarget.Reason) {
    Write-Host $terminalWindowTarget.Reason
}
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
$referenceCommand = New-InteractiveGatewayCommand `
    -RichEnabledVariable "REFERENCE_GATEWAY_TERMINAL_RICH_ENABLED" `
    -ScreenEnabledVariable "REFERENCE_GATEWAY_TERMINAL_SCREEN_ENABLED" `
    -Command ($referenceCommandLines -join [Environment]::NewLine)

$serviceTabs = @(
    [pscustomobject]@{
        Title = "News Gateway"
        Command = New-InteractiveGatewayCommand `
            -RichEnabledVariable "NEWS_TERMINAL_RICH_ENABLED" `
            -ScreenEnabledVariable "NEWS_TERMINAL_SCREEN_ENABLED" `
            -Command (
                "& " + (ConvertTo-PowerShellLiteral -Value $newsLauncher) +
                " -CondaEnv " + (ConvertTo-PowerShellLiteral -Value $CondaEnv) +
                " -PythonExe $pythonLiteral"
            )
    },
    [pscustomobject]@{
        Title = "SEC Gateway"
        Command = New-InteractiveGatewayCommand `
            -RichEnabledVariable "SEC_GATEWAY_TERMINAL_RICH_ENABLED" `
            -ScreenEnabledVariable "SEC_GATEWAY_TERMINAL_SCREEN_ENABLED" `
            -Command (
                "& " + (ConvertTo-PowerShellLiteral -Value $secLauncher) +
                " -CondaEnv " + (ConvertTo-PowerShellLiteral -Value $CondaEnv) +
                " -PythonExe $pythonLiteral"
            )
    },
    [pscustomobject]@{
        Title = "Reference Gateway"
        Command = $referenceCommand
    },
    [pscustomobject]@{
        Title = "IBKR Gateway Supervisor"
        Command = New-InteractiveGatewayCommand `
            -RichEnabledVariable "IBKR_GATEWAY_TERMINAL_RICH_ENABLED" `
            -ScreenEnabledVariable "IBKR_GATEWAY_TERMINAL_SCREEN_ENABLED" `
            -Command (
                "& " + (ConvertTo-PowerShellLiteral -Value $ibkrLauncher) +
                " -PythonExe $pythonLiteral" +
                " -Account " + (ConvertTo-PowerShellLiteral -Value $IbkrAccount)
            )
    },
    [pscustomobject]@{
        Title = "Text Intelligence"
        Command = (
            "& " + (ConvertTo-PowerShellLiteral -Value $textIntelligenceLauncher) +
            " -CondaEnv " + (ConvertTo-PowerShellLiteral -Value $CondaEnv) +
            " -PythonExe $pythonLiteral"
        )
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

function Open-ServiceTabs {
    param([object[]]$Tabs)

    if (-not $PSCmdlet.ShouldProcess(
        $terminalWindowTarget.Description,
        "Open $($Tabs.Count) independent PowerShell service tabs"
    )) {
        return $terminalWindowTarget
    }

    $dispatchTarget = Confirm-WindowsTerminalTarget `
        -Target $terminalWindowTarget `
        -RequestedMode $TerminalTarget `
        -FallbackWindowName $TerminalWindowName `
        -PythonExecutable $resolvedPython
    $terminalArguments = @("-w", $dispatchTarget.Window)
    for ($index = 0; $index -lt $Tabs.Count; $index++) {
        if ($index -gt 0) {
            $terminalArguments += ";"
        }
        $terminalArguments += @(
            "new-tab",
            "--title", $Tabs[$index].Title,
            "--suppressApplicationTitle",
            "-d", $repoRoot,
            $powerShellExe,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $serviceTabHost,
            "-EncodedCommand", (ConvertTo-PowerShellEncodedCommand -Command $Tabs[$index].Command),
            "-PowerShellExe", $powerShellExe
        )
    }

    & $resolvedWindowsTerminal @terminalArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Terminal failed to create the service tabs (exit code $LASTEXITCODE)."
    }
    return $dispatchTarget
}

$usedTerminalWindowTarget = Open-ServiceTabs -Tabs $serviceTabs

if ($WhatIfPreference) {
    Write-Host ""
    Write-Host "Validation complete; no Windows Terminal tabs were opened."
    return
}

Write-Host ""
Write-Host "Opened five independent PowerShell tabs in $($usedTerminalWindowTarget.Description), in this order:"
for ($index = 0; $index -lt $serviceTabs.Count; $index++) {
    Write-Host ("  {0}. {1}" -f ($index + 1), $serviceTabs[$index].Title)
}
Write-Host "This starter now exits instead of supervising the five launcher processes."
Write-Host "A successful graceful stop exits each tab host cleanly so Windows Terminal closes the service tabs."
Write-Host "Reference waits for IBKR Supervisor health, then at least $ReferenceDelaySeconds seconds, then ready/authenticated state."
Write-Host "Text Intelligence runs deterministic News/SEC V5 labeling only unless Live AI is explicitly enabled."
Write-Host "Stop all matching instances with scripts\stop_live_gateway_services.ps1."

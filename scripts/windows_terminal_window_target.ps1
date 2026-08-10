$env:PYTHONDONTWRITEBYTECODE = "1"

function Get-WindowsTerminalCallerWindow {
    param([string]$PythonExecutable)

    if ([string]::IsNullOrWhiteSpace($env:WT_SESSION)) {
        return [long]0
    }

    $windowProbe = @'
import ctypes

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_ulong),
]
window_handle = user32.GetForegroundWindow() or 0
process_id = ctypes.c_ulong()
user32.GetWindowThreadProcessId(window_handle, ctypes.byref(process_id))
print(f'{window_handle}|{process_id.value}')
'@
    $probeOutput = & $PythonExecutable -c $windowProbe 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $probeOutput) {
        return [long]0
    }

    $probeParts = ([string]$probeOutput).Trim().Split("|")
    [long]$windowHandle = 0
    [int]$windowProcessId = 0
    if ($probeParts.Count -ne 2 -or
        -not [long]::TryParse($probeParts[0], [ref]$windowHandle) -or
        -not [int]::TryParse($probeParts[1], [ref]$windowProcessId)) {
        return [long]0
    }

    $windowProcess = Get-Process -Id $windowProcessId -ErrorAction SilentlyContinue
    if ($null -eq $windowProcess -or
        $windowProcess.ProcessName -notin @("WindowsTerminal", "WindowsTerminalPreview")) {
        return [long]0
    }

    return $windowHandle
}

function New-NamedWindowsTerminalTarget {
    param(
        [string]$WindowName,
        [string]$Reason = ""
    )

    if (-not $WindowName.Trim()) {
        throw "-TerminalWindowName cannot be empty when a named Windows Terminal window is used."
    }
    $resolvedName = $WindowName.Trim()
    return [pscustomobject]@{
        Kind = "Named"
        Window = $resolvedName
        CallerWindowHandle = [long]0
        Description = "named Windows Terminal window '$resolvedName'"
        Reason = $Reason
    }
}

function Resolve-WindowsTerminalTarget {
    param(
        [ValidateSet("Auto", "Caller", "Named")]
        [string]$Mode,
        [string]$FallbackWindowName,
        [long]$CallerWindowHandle
    )

    if ($Mode -eq "Named") {
        return New-NamedWindowsTerminalTarget -WindowName $FallbackWindowName
    }
    if ($CallerWindowHandle -ne 0) {
        return [pscustomobject]@{
            Kind = "Caller"
            Window = "0"
            CallerWindowHandle = $CallerWindowHandle
            Description = "the invoking Windows Terminal window"
            Reason = ""
        }
    }
    if ($Mode -eq "Caller") {
        $sessionState = if ([string]::IsNullOrWhiteSpace($env:WT_SESSION)) {
            "WT_SESSION is not present"
        }
        else {
            "WT_SESSION is present, but the foreground window is not owned by Windows Terminal"
        }
        throw (
            "-TerminalTarget Caller requires the foreground calling window to be Windows Terminal; " +
            "$sessionState. A classic PowerShell/conhost window cannot contain tabs. " +
            "Run this command from a Windows Terminal tab or omit Caller to use the dedicated named window."
        )
    }
    return New-NamedWindowsTerminalTarget `
        -WindowName $FallbackWindowName `
        -Reason (
            "The current PowerShell prompt is not hosted by a verified foreground Windows Terminal window, " +
            "so it cannot accept tabs. Using the dedicated named Windows Terminal window instead."
        )
}

function Confirm-WindowsTerminalTarget {
    param(
        [Parameter(Mandatory)]
        $Target,
        [ValidateSet("Auto", "Caller", "Named")]
        [string]$RequestedMode,
        [string]$FallbackWindowName,
        [string]$PythonExecutable
    )

    if ($Target.Kind -ne "Caller") {
        return $Target
    }

    $windowActivation = @'
import ctypes
import sys
import time

window_handle = int(sys.argv[1])
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.ShowWindowAsync.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
if not user32.IsWindow(window_handle):
    raise SystemExit(2)
if user32.IsIconic(window_handle):
    user32.ShowWindowAsync(window_handle, 9)
user32.SetForegroundWindow(window_handle)
time.sleep(0.1)
raise SystemExit(0 if user32.GetForegroundWindow() == window_handle else 3)
'@
    & $PythonExecutable -c $windowActivation "$($Target.CallerWindowHandle)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        return $Target
    }

    if ($RequestedMode -eq "Caller") {
        throw "The invoking Windows Terminal window could not be reactivated, so exact caller-window routing is unavailable."
    }

    $fallback = New-NamedWindowsTerminalTarget `
        -WindowName $FallbackWindowName `
        -Reason "The captured Windows Terminal caller could not be reactivated."
    Write-Warning (
        "The invoking Windows Terminal window could not be reactivated. " +
        "Using $($fallback.Description) instead."
    )
    return $fallback
}

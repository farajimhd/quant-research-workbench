[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$EncodedCommand,
    [string]$PowerShellExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $PowerShellExe.Trim()) {
    $PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
}
elseif (-not (Test-Path -LiteralPath $PowerShellExe -PathType Leaf)) {
    throw "The requested PowerShell executable does not exist: $PowerShellExe"
}

# Windows Terminal's default closeOnExit behavior preserves a failed command
# tab but closes a tab whose root process exits successfully. Keep this host
# alive while the real launcher owns a child PowerShell process. A Ctrl+C sent
# to the shared console reaches the launcher and marks this host to return zero
# after that launcher exits, so an operator-requested graceful stop closes the
# tab without hiding an unrelated startup/runtime failure.
$consoleControlSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class ServiceTabConsoleControl
{
    private enum CtrlType : uint
    {
        CtrlC = 0,
        CtrlBreak = 1,
        CtrlClose = 2,
        CtrlLogoff = 5,
        CtrlShutdown = 6
    }

    private delegate bool HandlerRoutine(CtrlType ctrlType);
    private static HandlerRoutine handler;
    public static volatile bool StopRequested;

    [DllImport("Kernel32", SetLastError = true)]
    private static extern bool SetConsoleCtrlHandler(
        HandlerRoutine handlerRoutine,
        bool add
    );

    public static void Register()
    {
        handler = HandleConsoleControl;
        if (!SetConsoleCtrlHandler(handler, true))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    public static void Unregister()
    {
        if (handler != null)
        {
            SetConsoleCtrlHandler(handler, false);
            handler = null;
        }
    }

    private static bool HandleConsoleControl(CtrlType ctrlType)
    {
        if (ctrlType == CtrlType.CtrlC || ctrlType == CtrlType.CtrlBreak)
        {
            StopRequested = true;
            return true;
        }
        return false;
    }
}
'@

Add-Type -TypeDefinition $consoleControlSource -Language CSharp
[ServiceTabConsoleControl]::Register()

$child = $null
try {
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $PowerShellExe
    $startInfo.Arguments = (
        "-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand {0}" -f
        $EncodedCommand
    )
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $child = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $child) {
        throw "PowerShell did not return a child process for the service command."
    }

    while (-not $child.WaitForExit(250)) {
        # Wait in bounded intervals so Ctrl+C callbacks remain observable.
    }
    $child.Refresh()
    $childExitCode = $child.ExitCode
}
finally {
    [ServiceTabConsoleControl]::Unregister()
    if ($null -ne $child) {
        $child.Dispose()
    }
}

if ([ServiceTabConsoleControl]::StopRequested) {
    exit 0
}
exit $childExitCode

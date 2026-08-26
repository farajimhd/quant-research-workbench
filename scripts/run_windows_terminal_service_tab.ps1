[CmdletBinding()]
param(
    [string]$CommandPath = "",
    [string]$EncodedCommand = "",
    [string]$PowerShellExe = "",
    [string]$RegistryPath = "",
    [string]$ServiceRole = "",
    [ValidateRange(0, 65535)]
    [int]$ServicePort = 0,
    [string]$InstanceId = "",
    [string]$RepositoryRoot = "",
    [string]$DesiredFingerprint = "",
    [string]$LaunchMetadataPath = "",
    [string]$LaunchMetadataBase64 = "",
    [string]$LogRoot = "",
    [ValidateRange(1, 100)]
    [int]$LogRetentionRuns = 10
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
    private const int StdOutputHandle = -11;
    private const int StdErrorHandle = -12;
    private const uint EnableVirtualTerminalProcessing = 0x0004;

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

    [DllImport("Kernel32", SetLastError = true)]
    private static extern IntPtr GetStdHandle(int standardHandle);

    [DllImport("Kernel32", SetLastError = true)]
    private static extern bool GetConsoleMode(IntPtr consoleHandle, out uint mode);

    [DllImport("Kernel32", SetLastError = true)]
    private static extern bool SetConsoleMode(IntPtr consoleHandle, uint mode);

    public static void EnableVirtualTerminalOutput()
    {
        EnableVirtualTerminalOutput(StdOutputHandle);
        EnableVirtualTerminalOutput(StdErrorHandle);
    }

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

    private static void EnableVirtualTerminalOutput(int standardHandle)
    {
        IntPtr consoleHandle = GetStdHandle(standardHandle);
        if (consoleHandle == IntPtr.Zero || consoleHandle == new IntPtr(-1))
        {
            return;
        }
        uint mode;
        if (!GetConsoleMode(consoleHandle, out mode))
        {
            return;
        }
        SetConsoleMode(consoleHandle, mode | EnableVirtualTerminalProcessing);
    }
}

public static class ServiceTabJobControl
{
    private const uint JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        uint informationClass,
        IntPtr information,
        uint informationLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr Create(string name)
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, name);
        if (job == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        limits.BasicLimitInformation.LimitFlags = JobObjectLimitKillOnJobClose;
        int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr pointer = Marshal.AllocHGlobal(length);
        try
        {
            Marshal.StructureToPtr(limits, pointer, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, pointer, (uint)length))
            {
                int error = Marshal.GetLastWin32Error();
                CloseHandle(job);
                throw new Win32Exception(error);
            }
        }
        finally
        {
            Marshal.FreeHGlobal(pointer);
        }
        return job;
    }

    public static void Assign(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    public static void Close(IntPtr job)
    {
        if (job != IntPtr.Zero)
        {
            CloseHandle(job);
        }
    }
}
'@

Add-Type -TypeDefinition $consoleControlSource -Language CSharp
[ServiceTabConsoleControl]::EnableVirtualTerminalOutput()
[ServiceTabConsoleControl]::Register()

$child = $null
$job = [IntPtr]::Zero
$startGate = $null
$registered = $false
$stdoutWriter = $null
$stderrWriter = $null
$runRoot = ""
$stdoutPath = ""
$stderrPath = ""
$exitPath = ""
$bootstrapPath = ""
$runStartedAtUtc = [DateTime]::UtcNow
$childExitCode = 1
$exitReason = "host_exception"
$hostError = $null
try {
    if (-not $CommandPath.Trim() -and -not $EncodedCommand.Trim()) {
        throw "CommandPath or EncodedCommand must be supplied."
    }
    if ($CommandPath.Trim() -and $EncodedCommand.Trim()) {
        throw "CommandPath and EncodedCommand are mutually exclusive."
    }
    if ($LaunchMetadataPath.Trim() -and $LaunchMetadataBase64.Trim()) {
        throw "LaunchMetadataPath and LaunchMetadataBase64 are mutually exclusive."
    }

    $registrationValues = @($RegistryPath, $ServiceRole, $InstanceId, $RepositoryRoot)
    $registrationEnabled = @($registrationValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -gt 0
    if ($registrationEnabled -and
        @($registrationValues | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -gt 0) {
        throw "RegistryPath, ServiceRole, InstanceId, and RepositoryRoot must be supplied together."
    }

    $launchMetadata = [pscustomobject]@{
        fingerprint_components = @{}
        launch_inputs = @{}
    }
    if ($LaunchMetadataPath.Trim()) {
        $resolvedLaunchMetadataPath = [IO.Path]::GetFullPath($LaunchMetadataPath)
        if (-not (Test-Path -LiteralPath $resolvedLaunchMetadataPath -PathType Leaf)) {
            throw "Launch metadata does not exist: $resolvedLaunchMetadataPath"
        }
        $metadataText = Get-Content -LiteralPath $resolvedLaunchMetadataPath -Raw -Encoding UTF8
        $launchMetadata = $metadataText | ConvertFrom-Json
    }
    elseif ($LaunchMetadataBase64.Trim()) {
        $metadataText = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($LaunchMetadataBase64)
        )
        $launchMetadata = $metadataText | ConvertFrom-Json
    }

    if ($LogRoot.Trim()) {
        $resolvedRepositoryRootForLogs = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
        $resolvedLogRoot = [IO.Path]::GetFullPath($LogRoot).TrimEnd('\')
        if ($resolvedLogRoot.Equals($resolvedRepositoryRootForLogs, [StringComparison]::OrdinalIgnoreCase) -or
            $resolvedLogRoot.StartsWith($resolvedRepositoryRootForLogs + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Service logs must be outside the repository: $resolvedLogRoot"
        }
        $safeRole = if ($ServiceRole.Trim()) { $ServiceRole -replace '[^A-Za-z0-9_.-]', '_' } else { 'service' }
        $safeInstance = if ($InstanceId.Trim()) { $InstanceId -replace '[^A-Za-z0-9_.-]', '_' } else { [Guid]::NewGuid().ToString('N') }
        $runName = "{0}_{1}" -f ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')), $safeInstance
        $serviceLogRoot = Join-Path $resolvedLogRoot $safeRole
        $runRoot = Join-Path $serviceLogRoot $runName
        [IO.Directory]::CreateDirectory($runRoot) | Out-Null
        $stdoutPath = Join-Path $runRoot 'stdout.log'
        $stderrPath = Join-Path $runRoot 'stderr.log'
        $exitPath = Join-Path $runRoot 'exit.json'
        $runManifest = [ordered]@{
            schema_version = 1
            service_role = $ServiceRole
            service_port = $ServicePort
            instance_id = $InstanceId
            repository_root = $resolvedRepositoryRootForLogs
            desired_fingerprint = $DesiredFingerprint
            fingerprint_components = $launchMetadata.fingerprint_components
            launch_inputs = $launchMetadata.launch_inputs
            started_at_utc = $runStartedAtUtc.ToString('o')
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            exit_path = $exitPath
        }
        $runManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runRoot 'run.json') -Encoding UTF8
    }

    $jobName = "Local\QWServiceTabJob_$([Guid]::NewGuid().ToString('N'))"
    $job = [ServiceTabJobControl]::Create($jobName)
    $gateName = "Local\QWServiceTabGate_$([Guid]::NewGuid().ToString('N'))"
    $startGate = [Threading.EventWaitHandle]::new(
        $false,
        [Threading.EventResetMode]::ManualReset,
        $gateName
    )
    $gateLiteral = $gateName.Replace("'", "''")
    $commandPathLiteral = ""
    if ($CommandPath.Trim()) {
        $resolvedCommandPath = [IO.Path]::GetFullPath($CommandPath)
        if (-not (Test-Path -LiteralPath $resolvedCommandPath -PathType Leaf)) {
            throw "Service command does not exist: $resolvedCommandPath"
        }
        $commandPathLiteral = $resolvedCommandPath.Replace("'", "''")
    }
    $commandLiteral = $EncodedCommand.Replace("'", "''")
    $commandInvocation = if ($commandPathLiteral) {
        "& '$commandPathLiteral'"
    }
    else {
        "`$commandText = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('$commandLiteral'))`n& ([ScriptBlock]::Create(`$commandText))"
    }
    $bootstrapCommand = @"
`$gate = [Threading.EventWaitHandle]::OpenExisting('$gateLiteral')
try { [void]`$gate.WaitOne() } finally { `$gate.Dispose() }
`$ProgressPreference = 'SilentlyContinue'
$commandInvocation
if (`$null -ne `$LASTEXITCODE) { exit `$LASTEXITCODE }
if (-not `$?) { exit 1 }
"@
    $bootstrapPath = if ($runRoot) {
        Join-Path $runRoot 'bootstrap.ps1'
    }
    else {
        Join-Path ([IO.Path]::GetTempPath()) ("qw-service-bootstrap-{0}.ps1" -f [Guid]::NewGuid().ToString('N'))
    }
    $bootstrapCommand | Set-Content -LiteralPath $bootstrapPath -Encoding UTF8
    $bootstrapArgument = $bootstrapPath.Replace('"', '\"')

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $PowerShellExe
    $startInfo.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -File `"$bootstrapArgument`""
    $startInfo.UseShellExecute = $false
    # This host already owns the Windows Terminal tab's console. The launcher
    # must inherit that console so normal output, ANSI sequences, and Rich Live
    # rendering reach the tab. CREATE_NO_WINDOW detaches a console child and
    # silently discards the operational display.
    $startInfo.CreateNoWindow = $false
    if ($runRoot) {
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
    }
    $child = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $child) {
        throw "PowerShell did not return a child process for the service command."
    }
    [ServiceTabJobControl]::Assign($job, $child.Handle)

    if ($registrationEnabled) {
        $resolvedRepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
        $resolvedRegistryPath = [IO.Path]::GetFullPath($RegistryPath)
        if ($resolvedRegistryPath.StartsWith(
            $resolvedRepositoryRoot + '\',
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Workspace service ownership records must be outside the repository: $resolvedRegistryPath"
        }
        $registryDirectory = Split-Path -Parent $resolvedRegistryPath
        [IO.Directory]::CreateDirectory($registryDirectory) | Out-Null
        $hostProcess = Get-Process -Id $PID -ErrorAction Stop
        $child.Refresh()
        $record = [ordered]@{
            schema_version = 1
            instance_id = $InstanceId
            service_role = $ServiceRole
            service_port = $ServicePort
            repository_root = $resolvedRepositoryRoot
            registry_path = $resolvedRegistryPath
            host_pid = [int]$PID
            host_started_at_utc = $hostProcess.StartTime.ToUniversalTime().ToString("o")
            child_pid = [int]$child.Id
            child_started_at_utc = $child.StartTime.ToUniversalTime().ToString("o")
            job_name = $jobName
            terminal_session = [string]$env:WT_SESSION
            desired_fingerprint = $DesiredFingerprint
            fingerprint_components = $launchMetadata.fingerprint_components
            launch_inputs = $launchMetadata.launch_inputs
            run_log_root = $runRoot
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            exit_path = $exitPath
            registered_at_utc = [DateTime]::UtcNow.ToString("o")
        }
        $temporaryRegistryPath = "$resolvedRegistryPath.$PID.tmp"
        $record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryRegistryPath -Encoding UTF8
        Move-Item -LiteralPath $temporaryRegistryPath -Destination $resolvedRegistryPath -Force
        $RegistryPath = $resolvedRegistryPath
        $registered = $true
    }

    [void]$startGate.Set()

    if ($runRoot) {
        $stdoutWriter = [IO.StreamWriter]::new($stdoutPath, $true, [Text.UTF8Encoding]::new($false))
        $stderrWriter = [IO.StreamWriter]::new($stderrPath, $true, [Text.UTF8Encoding]::new($false))
        $stdoutRead = $child.StandardOutput.ReadLineAsync()
        $stderrRead = $child.StandardError.ReadLineAsync()
        $stdoutEof = $false
        $stderrEof = $false
        while (-not $child.HasExited -or -not $stdoutEof -or -not $stderrEof) {
            if (-not $stdoutEof -and $stdoutRead.IsCompleted) {
                $line = $stdoutRead.GetAwaiter().GetResult()
                if ($null -ne $line) {
                    [Console]::Out.WriteLine($line)
                    $stdoutWriter.WriteLine($line)
                    $stdoutWriter.Flush()
                    $stdoutRead = $child.StandardOutput.ReadLineAsync()
                }
                else {
                    $stdoutEof = $true
                }
            }
            if (-not $stderrEof -and $stderrRead.IsCompleted) {
                $line = $stderrRead.GetAwaiter().GetResult()
                if ($null -ne $line) {
                    # Windows PowerShell prefixes redirected error streams with
                    # CLIXML transport metadata. Preserve the human diagnostic
                    # line, not serialization/progress envelopes.
                    if ($line -ne '#< CLIXML' -and -not $line.StartsWith('<Objs ')) {
                        [Console]::Error.WriteLine($line)
                        $stderrWriter.WriteLine($line)
                        $stderrWriter.Flush()
                    }
                    $stderrRead = $child.StandardError.ReadLineAsync()
                }
                else {
                    $stderrEof = $true
                }
            }
            Start-Sleep -Milliseconds 50
        }
        $child.WaitForExit()
    }
    else {
        while (-not $child.WaitForExit(250)) {
            # Wait in bounded intervals so Ctrl+C callbacks remain observable.
        }
    }
    $child.Refresh()
    $childExitCode = $child.ExitCode
    $exitReason = if ([ServiceTabConsoleControl]::StopRequested) {
        'operator_stop'
    } elseif ($childExitCode -eq 0) {
        'process_exit_zero'
    } else {
        'process_exit_nonzero'
    }
}
catch {
    $hostError = $_
    $childExitCode = 1
    $exitReason = 'host_exception'
}
finally {
    [ServiceTabConsoleControl]::Unregister()
    if ($null -ne $startGate) {
        $startGate.Dispose()
    }
    if ($null -ne $child) {
        $child.Dispose()
    }
    if ($null -ne $stdoutWriter) { $stdoutWriter.Dispose() }
    if ($null -ne $stderrWriter) { $stderrWriter.Dispose() }
    if ($bootstrapPath -and (Test-Path -LiteralPath $bootstrapPath -PathType Leaf)) {
        Remove-Item -LiteralPath $bootstrapPath -Force -ErrorAction SilentlyContinue
    }
    [ServiceTabJobControl]::Close($job)
    if ($runRoot) {
        $exitRecord = [ordered]@{
            schema_version = 1
            service_role = $ServiceRole
            service_port = $ServicePort
            instance_id = $InstanceId
            started_at_utc = $runStartedAtUtc.ToString('o')
            finished_at_utc = [DateTime]::UtcNow.ToString('o')
            exit_code = $childExitCode
            reason = $exitReason
            stop_requested = [ServiceTabConsoleControl]::StopRequested
            error = if ($null -ne $hostError) { [string]$hostError.Exception.Message } else { '' }
        }
        $exitRecord | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $exitPath -Encoding UTF8
        $serviceLogRoot = Split-Path -Parent $runRoot
        $expired = @(Get-ChildItem -LiteralPath $serviceLogRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -Skip $LogRetentionRuns)
        foreach ($directory in $expired) {
            $resolvedExpired = [IO.Path]::GetFullPath($directory.FullName)
            if ($resolvedExpired.StartsWith($serviceLogRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolvedExpired -Recurse -Force
            }
        }
    }
    if ($registered -and (Test-Path -LiteralPath $RegistryPath -PathType Leaf)) {
        Remove-Item -LiteralPath $RegistryPath -Force -ErrorAction SilentlyContinue
    }
}

if ($null -ne $hostError) {
    [Console]::Error.WriteLine($hostError.Exception.Message)
    exit 1
}
if ([ServiceTabConsoleControl]::StopRequested) {
    exit 0
}
exit $childExitCode

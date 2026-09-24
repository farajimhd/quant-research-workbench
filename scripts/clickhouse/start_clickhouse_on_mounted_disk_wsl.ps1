param(
    [string]$Distro = "Ubuntu",
    [string]$PhysicalDrive = "\\.\PHYSICALDRIVE1",
    [string]$SipRawSsdPhysicalDrive = "\\.\PHYSICALDRIVE2",
    [string]$LiveMarketSsdPhysicalDrive = "\\.\PHYSICALDRIVE3",

    # =============================================================================
    # Expected UUID of the large ext4 partition that should hold ClickHouse data.
    # This is used as a final safety check after candidate partition discovery.
    # =============================================================================
    [string]$ExpectedDataFsUuid = "0b8fbd31-d3b1-4fd2-9b37-2cfde1141440",
    [string]$ExpectedSipRawSsdFsUuid = "cd30bb2c-06c1-4d86-9244-a4bbb8dbe62f",
    [string]$ExpectedLiveMarketSsdFsUuid = "b7e5ab72-f605-4dbd-8130-6a37c4ce44df",

    # =============================================================================
    # Local-network ClickHouse access policy rendered into users.d/config.d by the
    # Linux bootstrap. Keep secrets in environment variables or pass them at launch.
    # =============================================================================
    [string]$WorkstationLanIp = "",
    [string]$AdminPasswordSha256Hex = "",
    [string]$TradingDashboardAppPasswordSha256Hex = "",
    [string]$TradingDashboardReadDatabases = "",
    [string]$TradingDashboardWriteDatabases = "",
    [string]$AllowTradingDashboardCreateDatabase = "",
    [string]$AllowTradingDashboardFileRead = "",
    [string]$AllowTradingDashboardSystemFlushLogs = "",
    [string]$QuantResearchWorkbenchIp = "",
    [string]$QuantResearchWorkbenchPasswordSha256Hex = "",
    [string]$QuantResearchWorkbenchReadDatabases = "",
    [string]$QuantResearchWorkbenchWriteDatabases = "",
    [string]$AllowQuantResearchWorkbenchCreateDatabase = "",

    # =============================================================================
    # Set only when you intentionally want to re-run the expensive recursive
    # ownership repair across the entire ClickHouse data directory.
    # =============================================================================
    [string]$ForcePermissionRepair = "",

    # =============================================================================
    # Maximum time to wait for ClickHouse to open its native or HTTP port after
    # startup. Large local datasets can need more than a few seconds to load parts.
    # =============================================================================
    [string]$StartupReadyTimeoutSeconds = "",

    # The runtime copy contains code only. Keep password hashes in the
    # workstation's existing secret settings file, never beside this script.
    [string]$SettingsPath = "",

    # =============================================================================
    # Internal override used by operator wrappers that need a temporary config.d
    # or users.d overlay while keeping this script's disk and mount validation.
    # =============================================================================
    [string]$RepoClickHouseDirOverride = ""
)

$ErrorActionPreference = "Stop"

function Import-RepoDotEnv {
    param(
        [string]$Path
    )

    $Values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $Values
    }

    foreach ($Line in Get-Content -LiteralPath $Path) {
        $Trimmed = $Line.Trim()
        if ($Trimmed.Length -eq 0 -or $Trimmed.StartsWith("#")) {
            continue
        }

        $EqualsIndex = $Trimmed.IndexOf("=")
        if ($EqualsIndex -le 0) {
            continue
        }

        $Name = $Trimmed.Substring(0, $EqualsIndex).Trim()
        $Value = $Trimmed.Substring($EqualsIndex + 1).Trim()

        if ($Value.Length -ge 2) {
            $First = $Value.Substring(0, 1)
            $Last = $Value.Substring($Value.Length - 1, 1)
            if (($First -eq '"' -and $Last -eq '"') -or ($First -eq "'" -and $Last -eq "'")) {
                $Value = $Value.Substring(1, $Value.Length - 2)
            }
        }

        $Values[$Name] = $Value
    }
    return $Values
}

function Resolve-Setting {
    param(
        [string]$ParameterValue,
        [string[]]$EnvironmentNames,
        [hashtable]$DotEnvValues,
        [string]$Default = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ParameterValue)) {
        return $ParameterValue.Trim()
    }

    foreach ($Name in $EnvironmentNames) {
        if ($null -ne $DotEnvValues -and $DotEnvValues.ContainsKey($Name)) {
            $Value = $DotEnvValues[$Name]
            if (-not [string]::IsNullOrWhiteSpace($Value)) {
                return $Value.Trim()
            }
        }
    }

    foreach ($Name in $EnvironmentNames) {
        $Value = [Environment]::GetEnvironmentVariable($Name, "Process")
        if (-not [string]::IsNullOrWhiteSpace($Value)) {
            return $Value.Trim()
        }
    }

    return $Default
}

function Resolve-BoolSetting {
    param(
        [string]$ParameterValue,
        [string[]]$EnvironmentNames,
        [hashtable]$DotEnvValues,
        [bool]$Default = $false
    )

    $RawValue = Resolve-Setting `
        -ParameterValue $ParameterValue `
        -EnvironmentNames $EnvironmentNames `
        -DotEnvValues $DotEnvValues `
        -Default ""
    if ([string]::IsNullOrWhiteSpace($RawValue)) {
        return $Default
    }

    switch -Regex ($RawValue.Trim().ToLowerInvariant()) {
        '^(1|true|yes|y|on)$' { return $true }
        '^(0|false|no|n|off)$' { return $false }
        default { throw "Boolean setting must be true/false, yes/no, on/off, or 1/0. Received: $RawValue" }
    }
}

function Resolve-PositiveIntegerSetting {
    param(
        [string]$ParameterValue,
        [string[]]$EnvironmentNames,
        [hashtable]$DotEnvValues,
        [int]$Default
    )

    $RawValue = Resolve-Setting `
        -ParameterValue $ParameterValue `
        -EnvironmentNames $EnvironmentNames `
        -DotEnvValues $DotEnvValues `
        -Default ([string]$Default)

    if ($RawValue -notmatch '^[1-9][0-9]*$') {
        throw "Positive integer setting must be greater than zero. Received: $RawValue"
    }

    return [int]$RawValue
}

function Assert-IPv4Literal {
    param(
        [string]$Name,
        [string]$Value
    )

    if ($Value -notmatch '^(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})(\.(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}$') {
        throw "$Name must be an IPv4 literal. Received: $Value"
    }
}

function Assert-ClickHouseIdentifier {
    param(
        [string]$Name,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "$Name must be a non-empty unquoted ClickHouse identifier. Received: $Value"
    }
}

function Assert-ClickHouseIdentifierList {
    param(
        [string]$Name,
        [string]$Value,
        [bool]$Required = $true
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        if (-not $Required) {
            return
        }
        throw "$Name must contain at least one comma-separated ClickHouse identifier."
    }

    $Identifiers = $Value.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if ($Identifiers.Count -eq 0) {
        throw "$Name must contain at least one comma-separated ClickHouse identifier."
    }

    foreach ($Identifier in $Identifiers) {
        Assert-ClickHouseIdentifier -Name "$Name entry" -Value $Identifier
    }
}

function Assert-Sha256Hex {
    param(
        [string]$Name,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^[0-9A-Fa-f]{64}$') {
        throw "$Name must be a 64-character SHA-256 hex value. Set the matching CLICKHOUSE_*_PASSWORD_SHA256_HEX .env value or pass the matching PowerShell parameter."
    }
}

function ConvertTo-WslPath {
    param(
        [string]$WindowsPath
    )

    $PathForWsl = $WindowsPath -replace '\\', '/'
    return (wsl -d $Distro -- wslpath -a "$PathForWsl").Trim()
}

function Invoke-WslNativeCommand {
    param(
        [string[]]$Arguments
    )

    # Windows PowerShell wraps native stderr as error records. systemctl writes
    # successful SysV synchronization notices to stderr, so temporarily allow
    # those records through and use the native exit code as failure authority.
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & wsl @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $NativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    return $NativeExitCode
}

function Assert-LocalOnlyKeeperPort {
    # The embedded Keeper listener inherits ClickHouse's 0.0.0.0 bind. Until
    # TLS and per-znode ACLs are deployed, the Windows host must reject remote
    # inbound connections even if WSL networking or port forwarding changes.
    $RuleName = "QuantWorkbench-ClickHouse-Keeper-Block-9181"
    $Rule = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
    if ($null -eq $Rule) {
        New-NetFirewallRule -Name $RuleName -DisplayName "Block remote ClickHouse Keeper 9181" `
            -Direction Inbound -Action Block -Enabled True -Profile Any `
            -Protocol TCP -LocalPort 9181 | Out-Null
        $Rule = Get-NetFirewallRule -Name $RuleName -ErrorAction Stop
    }
    $Port = $Rule | Get-NetFirewallPortFilter
    if ($Rule.Enabled -ne "True" -or $Rule.Direction -ne "Inbound" -or
        $Rule.Action -ne "Block" -or $Port.Protocol -ne "TCP" -or
        $Port.LocalPort -ne "9181") {
        throw "Keeper firewall rule is not an enabled inbound TCP 9181 block."
    }
    Write-Host "Keeper safety: remote inbound TCP 9181 is blocked; local ClickHouse access remains available."
}

$RepoClickHouseDirWindows = $PSScriptRoot
$EffectiveRepoClickHouseDirWindows = $RepoClickHouseDirWindows
if (-not [string]::IsNullOrWhiteSpace($RepoClickHouseDirOverride)) {
    $EffectiveRepoClickHouseDirWindows = (Resolve-Path -LiteralPath $RepoClickHouseDirOverride.Trim()).Path
}

$RepoRootWindows = (Resolve-Path -LiteralPath (Join-Path $RepoClickHouseDirWindows "..\..")).Path
$RepoDotEnvPath = $SettingsPath
if ([string]::IsNullOrWhiteSpace($RepoDotEnvPath)) {
    $RepoDotEnvPath = [Environment]::GetEnvironmentVariable("CLICKHOUSE_MANAGED_SETTINGS_PATH", "Process")
}
if ([string]::IsNullOrWhiteSpace($RepoDotEnvPath)) {
    $RepoDotEnvPath = Join-Path $RepoRootWindows ".env"
}
if (-not (Test-Path -LiteralPath $RepoDotEnvPath)) {
    # Preserve the workstation's established secret authority during the
    # launcher move. This file is read, not copied to code or runtime output.
    $LegacySettingsPath = "D:\quant_code\trading-dashboard\.env"
    if (Test-Path -LiteralPath $LegacySettingsPath) {
        $RepoDotEnvPath = $LegacySettingsPath
    }
}
if (-not (Test-Path -LiteralPath $RepoDotEnvPath)) {
    throw "ClickHouse settings file is missing. Pass -SettingsPath with the workstation secret .env path."
}
$RepoDotEnvValues = Import-RepoDotEnv -Path $RepoDotEnvPath

$WorkstationLanIp = Resolve-Setting `
    -ParameterValue $WorkstationLanIp `
    -EnvironmentNames @("CLICKHOUSE_WORKSTATION_LAN_IP") `
    -DotEnvValues $RepoDotEnvValues `
    -Default "192.168.0.21"
$AdminPasswordSha256Hex = Resolve-Setting `
    -ParameterValue $AdminPasswordSha256Hex `
    -EnvironmentNames @("CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX") `
    -DotEnvValues $RepoDotEnvValues
$TradingDashboardAppPasswordSha256Hex = Resolve-Setting `
    -ParameterValue $TradingDashboardAppPasswordSha256Hex `
    -EnvironmentNames @("CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX") `
    -DotEnvValues $RepoDotEnvValues
$TradingDashboardReadDatabases = Resolve-Setting `
    -ParameterValue $TradingDashboardReadDatabases `
    -EnvironmentNames @("CLICKHOUSE_TRADING_DASHBOARD_READ_DATABASES") `
    -DotEnvValues $RepoDotEnvValues `
    -Default "trading_dashboard_dev,trading_dashboard_semantic_graph_review"
$TradingDashboardWriteDatabases = Resolve-Setting `
    -ParameterValue $TradingDashboardWriteDatabases `
    -EnvironmentNames @("CLICKHOUSE_TRADING_DASHBOARD_WRITE_DATABASES") `
    -DotEnvValues $RepoDotEnvValues `
    -Default "trading_dashboard_dev,trading_dashboard_semantic_graph_review"
$EffectiveAllowTradingDashboardCreateDatabase = Resolve-BoolSetting `
    -ParameterValue $AllowTradingDashboardCreateDatabase `
    -EnvironmentNames @("CLICKHOUSE_TRADING_DASHBOARD_ALLOW_CREATE_DATABASE") `
    -DotEnvValues $RepoDotEnvValues `
    -Default $true
$EffectiveAllowTradingDashboardFileRead = Resolve-BoolSetting `
    -ParameterValue $AllowTradingDashboardFileRead `
    -EnvironmentNames @("CLICKHOUSE_TRADING_DASHBOARD_ALLOW_FILE_READ") `
    -DotEnvValues $RepoDotEnvValues `
    -Default $true
$EffectiveAllowTradingDashboardSystemFlushLogs = Resolve-BoolSetting `
    -ParameterValue $AllowTradingDashboardSystemFlushLogs `
    -EnvironmentNames @("CLICKHOUSE_TRADING_DASHBOARD_ALLOW_SYSTEM_FLUSH_LOGS") `
    -DotEnvValues $RepoDotEnvValues `
    -Default $true
$QuantResearchWorkbenchIp = Resolve-Setting `
    -ParameterValue $QuantResearchWorkbenchIp `
    -EnvironmentNames @("CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP", "CLICKHOUSE_LAPTOP_APP_IP") `
    -DotEnvValues $RepoDotEnvValues `
    -Default "192.168.0.20"
$QuantResearchWorkbenchPasswordSha256Hex = Resolve-Setting `
    -ParameterValue $QuantResearchWorkbenchPasswordSha256Hex `
    -EnvironmentNames @("CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX", "CLICKHOUSE_LAPTOP_APP_PASSWORD_SHA256_HEX") `
    -DotEnvValues $RepoDotEnvValues
$QuantResearchWorkbenchReadDatabases = Resolve-Setting `
    -ParameterValue $QuantResearchWorkbenchReadDatabases `
    -EnvironmentNames @("CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES", "CLICKHOUSE_LAPTOP_READ_DATABASES", "CLICKHOUSE_LAPTOP_READ_DATABASE") `
    -DotEnvValues $RepoDotEnvValues
$QuantResearchWorkbenchWriteDatabases = Resolve-Setting `
    -ParameterValue $QuantResearchWorkbenchWriteDatabases `
    -EnvironmentNames @("CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES", "CLICKHOUSE_LAPTOP_WRITE_DATABASES", "CLICKHOUSE_LAPTOP_WRITE_DATABASE") `
    -DotEnvValues $RepoDotEnvValues `
    -Default "q_live"
$EffectiveAllowQuantResearchWorkbenchCreateDatabase = Resolve-BoolSetting `
    -ParameterValue $AllowQuantResearchWorkbenchCreateDatabase `
    -EnvironmentNames @("CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE", "CLICKHOUSE_LAPTOP_ALLOW_CREATE_DATABASE") `
    -DotEnvValues $RepoDotEnvValues `
    -Default $false
$EffectiveForcePermissionRepair = Resolve-BoolSetting `
    -ParameterValue $ForcePermissionRepair `
    -EnvironmentNames @("CLICKHOUSE_FORCE_PERMISSION_REPAIR") `
    -DotEnvValues $RepoDotEnvValues `
    -Default $false
$EffectiveStartupReadyTimeoutSeconds = Resolve-PositiveIntegerSetting `
    -ParameterValue $StartupReadyTimeoutSeconds `
    -EnvironmentNames @("CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS") `
    -DotEnvValues $RepoDotEnvValues `
    -Default 300

Assert-IPv4Literal -Name "WorkstationLanIp" -Value $WorkstationLanIp
Assert-IPv4Literal -Name "QuantResearchWorkbenchIp" -Value $QuantResearchWorkbenchIp
Assert-Sha256Hex -Name "AdminPasswordSha256Hex" -Value $AdminPasswordSha256Hex
Assert-Sha256Hex -Name "TradingDashboardAppPasswordSha256Hex" -Value $TradingDashboardAppPasswordSha256Hex
Assert-Sha256Hex -Name "QuantResearchWorkbenchPasswordSha256Hex" -Value $QuantResearchWorkbenchPasswordSha256Hex
Assert-ClickHouseIdentifierList -Name "QuantResearchWorkbenchReadDatabases" -Value $QuantResearchWorkbenchReadDatabases -Required $false
Assert-ClickHouseIdentifierList -Name "QuantResearchWorkbenchWriteDatabases" -Value $QuantResearchWorkbenchWriteDatabases

$BootstrapScriptWindows = Join-Path $RepoClickHouseDirWindows "clickhouse_bootstrap.sh"
$EffectiveConfigDirWindows = Join-Path $EffectiveRepoClickHouseDirWindows "config.d"
$EffectiveUsersDirWindows = Join-Path $EffectiveRepoClickHouseDirWindows "users.d"

if (-not (Test-Path -LiteralPath $BootstrapScriptWindows)) {
    throw "ClickHouse bootstrap script not found: $BootstrapScriptWindows"
}
if (-not (Test-Path -LiteralPath $EffectiveConfigDirWindows)) {
    throw "ClickHouse config.d overlay directory not found: $EffectiveConfigDirWindows"
}
if (-not (Test-Path -LiteralPath $EffectiveUsersDirWindows)) {
    throw "ClickHouse users.d overlay directory not found: $EffectiveUsersDirWindows"
}

Assert-LocalOnlyKeeperPort

Write-Host "==== Step 1: Ensure WSL distro is reachable ===="
wsl -d $Distro --cd / -- echo "WSL distro reachable." | Out-Null

# =============================================================================
# The ClickHouse package enables a systemd unit with Restart=always. Disable it
# before disk attachment work so it cannot open the default data paths while
# the repository-managed external disks are still unavailable.
# =============================================================================
# Equivalent WSL command: systemctl disable --now clickhouse-server.service
Write-Host "==== Step 2: Disable competing ClickHouse systemd startup ===="
$SystemdDisableExitCode = Invoke-WslNativeCommand -Arguments @(
    "-d", $Distro, "-u", "root", "--",
    "systemctl", "disable", "--now", "clickhouse-server.service"
)
if ($SystemdDisableExitCode -ne 0) {
    throw "Could not disable the competing ClickHouse systemd unit in WSL distro '$Distro'."
}


$RepoClickHouseDirWsl = ConvertTo-WslPath -WindowsPath $EffectiveRepoClickHouseDirWindows
$BootstrapScriptWsl = ConvertTo-WslPath -WindowsPath $BootstrapScriptWindows

Write-Host "==== Step 3: Detach any stale previous attachment (best effort) ===="
$PhysicalDrivesToMount = @(
    $PhysicalDrive,
    $SipRawSsdPhysicalDrive,
    $LiveMarketSsdPhysicalDrive
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

foreach ($Drive in $PhysicalDrivesToMount) {
    try {
        wsl --unmount $Drive | Out-Null
    } catch {
    }
}

Start-Sleep -Seconds 1

Write-Host "==== Step 4: Attach physical disks to WSL in raw mode (--bare) ===="
foreach ($Drive in $PhysicalDrivesToMount) {
    Write-Host "Attaching $Drive"
    wsl --mount $Drive --bare
}

# =============================================================================
# Give WSL / Linux a short moment to enumerate the newly attached disk.
# =============================================================================
Start-Sleep -Seconds 4

Write-Host "==== Step 5: Run Linux bootstrap file ===="

# =============================================================================
# Pass the expected UUID into the Linux bootstrap through environment variables.
# =============================================================================

$BootstrapEnv = @(
    "EXPECTED_DATA_FS_UUID=$ExpectedDataFsUuid",
    "EXPECTED_SIP_RAW_SSD_FS_UUID=$ExpectedSipRawSsdFsUuid",
    "EXPECTED_LIVE_MARKET_SSD_FS_UUID=$ExpectedLiveMarketSsdFsUuid",
    "REPO_CLICKHOUSE_DIR=$RepoClickHouseDirWsl",
    "CLICKHOUSE_WORKSTATION_LAN_IP=$WorkstationLanIp",
    "CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX=$AdminPasswordSha256Hex",
    "CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX=$TradingDashboardAppPasswordSha256Hex",
    "CLICKHOUSE_TRADING_DASHBOARD_READ_DATABASES=$TradingDashboardReadDatabases",
    "CLICKHOUSE_TRADING_DASHBOARD_WRITE_DATABASES=$TradingDashboardWriteDatabases",
    "CLICKHOUSE_TRADING_DASHBOARD_ALLOW_CREATE_DATABASE=$EffectiveAllowTradingDashboardCreateDatabase",
    "CLICKHOUSE_TRADING_DASHBOARD_ALLOW_FILE_READ=$EffectiveAllowTradingDashboardFileRead",
    "CLICKHOUSE_TRADING_DASHBOARD_ALLOW_SYSTEM_FLUSH_LOGS=$EffectiveAllowTradingDashboardSystemFlushLogs",
    "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP=$QuantResearchWorkbenchIp",
    "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX=$QuantResearchWorkbenchPasswordSha256Hex",
    "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES=$QuantResearchWorkbenchReadDatabases",
    "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES=$QuantResearchWorkbenchWriteDatabases",
    "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE=$EffectiveAllowQuantResearchWorkbenchCreateDatabase",
    "CLICKHOUSE_FORCE_PERMISSION_REPAIR=$EffectiveForcePermissionRepair",
    "CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS=$EffectiveStartupReadyTimeoutSeconds"
)

wsl -d $Distro -u root --cd / -- env @BootstrapEnv bash "$BootstrapScriptWsl"
if ($LASTEXITCODE -ne 0) {
    throw "ClickHouse bootstrap failed in WSL distro '$Distro' with exit code $LASTEXITCODE."
}

Write-Host "==== Done ===="

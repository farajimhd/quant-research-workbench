#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Hardened ClickHouse bootstrap for WSL
#
# Canonical storage model:
#   mounted big disk      -> /data
#   ClickHouse real home  -> /data/clickhouse
#   ClickHouse service    -> /var/lib/clickhouse (bind mount from /data/clickhouse)
#
# This script is intentionally strict:
#   - it stops ClickHouse before touching mounts
#   - it rejects non-canonical mount states
#   - it recreates the canonical bind mount every time
#   - it refuses startup if validation fails
# ==============================================================================

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
DATA_MOUNT="/data"
CH_DIR_ON_DATA="/data/clickhouse"
CH_TARGET="/var/lib/clickhouse"
SIP_RAW_SSD_MOUNT="/clickhouse-disks/sip-raw"
SIP_RAW_SSD_CLICKHOUSE_DIR="$SIP_RAW_SSD_MOUNT/clickhouse"
LIVE_MARKET_SSD_MOUNT="/clickhouse-disks/live-market"
LIVE_MARKET_SSD_CLICKHOUSE_DIR="$LIVE_MARKET_SSD_MOUNT/clickhouse"
CLICKHOUSE_BIN="/usr/bin/clickhouse"
CLICKHOUSE_CONFIG="/etc/clickhouse-server/config.xml"
RUNTIME_LOG_DIR="/mnt/d/TradingML/runtimes/clickhouse-managed"
BOOT_LOG="$RUNTIME_LOG_DIR/bootstrap.log"
SENTINEL_FILE=".clickhouse_mount_sentinel"
USER_FILES_PATH="/mnt/d/market-data"
EXPECTED_DATA_FS_UUID="${EXPECTED_DATA_FS_UUID:-}"
EXPECTED_SIP_RAW_SSD_FS_UUID="${EXPECTED_SIP_RAW_SSD_FS_UUID:-}"
EXPECTED_LIVE_MARKET_SSD_FS_UUID="${EXPECTED_LIVE_MARKET_SSD_FS_UUID:-}"
REPO_CLICKHOUSE_DIR="${REPO_CLICKHOUSE_DIR:-}"
CLICKHOUSE_WORKSTATION_LAN_IP="${CLICKHOUSE_WORKSTATION_LAN_IP:-}"
CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP="${CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP:-}"
CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX="${CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX:-}"
CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX="${CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX:-}"
CLICKHOUSE_TRADING_DASHBOARD_READ_DATABASES="${CLICKHOUSE_TRADING_DASHBOARD_READ_DATABASES:-trading_dashboard_dev,trading_dashboard_semantic_graph_review}"
CLICKHOUSE_TRADING_DASHBOARD_WRITE_DATABASES="${CLICKHOUSE_TRADING_DASHBOARD_WRITE_DATABASES:-trading_dashboard_dev,trading_dashboard_semantic_graph_review}"
CLICKHOUSE_TRADING_DASHBOARD_ALLOW_CREATE_DATABASE="${CLICKHOUSE_TRADING_DASHBOARD_ALLOW_CREATE_DATABASE:-true}"
CLICKHOUSE_TRADING_DASHBOARD_ALLOW_FILE_READ="${CLICKHOUSE_TRADING_DASHBOARD_ALLOW_FILE_READ:-true}"
CLICKHOUSE_TRADING_DASHBOARD_ALLOW_SYSTEM_FLUSH_LOGS="${CLICKHOUSE_TRADING_DASHBOARD_ALLOW_SYSTEM_FLUSH_LOGS:-true}"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP="${CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP:-${CLICKHOUSE_LAPTOP_APP_IP:-}}"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX="${CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX:-${CLICKHOUSE_LAPTOP_APP_PASSWORD_SHA256_HEX:-}}"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES="${CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES:-${CLICKHOUSE_LAPTOP_READ_DATABASES:-${CLICKHOUSE_LAPTOP_READ_DATABASE:-}}}"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES="${CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES:-${CLICKHOUSE_LAPTOP_WRITE_DATABASES:-${CLICKHOUSE_LAPTOP_WRITE_DATABASE:-q_live}}}"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE="${CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE:-${CLICKHOUSE_LAPTOP_ALLOW_CREATE_DATABASE:-false}}"
CLICKHOUSE_FORCE_PERMISSION_REPAIR="${CLICKHOUSE_FORCE_PERMISSION_REPAIR:-false}"
CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS="${CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS:-300}"
CLICKHOUSE_PROCESS_PATTERN="clickhouse-watchdog|ClickHouseWatchdog|clickhouse-server|clickhouse server"

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
mkdir -p "$RUNTIME_LOG_DIR"
exec > >(tee -a "$BOOT_LOG") 2>&1

echo "======================================================================"
echo "ClickHouse WSL bootstrap started at: $(date -Is)"
echo "Expected data filesystem UUID: ${EXPECTED_DATA_FS_UUID:-<missing>}"
echo "======================================================================"

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
normalize_token() {
    printf '%s' "$1" | tr -d '\r\n[:space:]'
}

EXPECTED_DATA_FS_UUID="$(normalize_token "$EXPECTED_DATA_FS_UUID")"
EXPECTED_SIP_RAW_SSD_FS_UUID="$(normalize_token "$EXPECTED_SIP_RAW_SSD_FS_UUID")"
EXPECTED_LIVE_MARKET_SSD_FS_UUID="$(normalize_token "$EXPECTED_LIVE_MARKET_SSD_FS_UUID")"
REPO_CLICKHOUSE_DIR="$(normalize_token "$REPO_CLICKHOUSE_DIR")"
CLICKHOUSE_WORKSTATION_LAN_IP="$(normalize_token "$CLICKHOUSE_WORKSTATION_LAN_IP")"
CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP="$(normalize_token "$CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP")"
CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX="$(normalize_token "$CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX")"
CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX="$(normalize_token "$CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX")"
CLICKHOUSE_TRADING_DASHBOARD_READ_DATABASES="$(normalize_token "$CLICKHOUSE_TRADING_DASHBOARD_READ_DATABASES")"
CLICKHOUSE_TRADING_DASHBOARD_WRITE_DATABASES="$(normalize_token "$CLICKHOUSE_TRADING_DASHBOARD_WRITE_DATABASES")"
CLICKHOUSE_TRADING_DASHBOARD_ALLOW_CREATE_DATABASE="$(normalize_token "$CLICKHOUSE_TRADING_DASHBOARD_ALLOW_CREATE_DATABASE")"
CLICKHOUSE_TRADING_DASHBOARD_ALLOW_FILE_READ="$(normalize_token "$CLICKHOUSE_TRADING_DASHBOARD_ALLOW_FILE_READ")"
CLICKHOUSE_TRADING_DASHBOARD_ALLOW_SYSTEM_FLUSH_LOGS="$(normalize_token "$CLICKHOUSE_TRADING_DASHBOARD_ALLOW_SYSTEM_FLUSH_LOGS")"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP="$(normalize_token "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP")"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX="$(normalize_token "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX")"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES="$(normalize_token "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES")"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES="$(normalize_token "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES")"
CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE="$(normalize_token "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE")"
CLICKHOUSE_FORCE_PERMISSION_REPAIR="$(normalize_token "$CLICKHOUSE_FORCE_PERMISSION_REPAIR")"
CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS="$(normalize_token "$CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS")"

die() {
    echo "ERROR: $*"
    exit 1
}

assert_positive_integer() {
    local name="$1"
    local value="$2"

    [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$name must be a positive integer. Received: $value"
}

log_section() {
    echo
    echo "----------------------------------------------------------------------"
    echo "$1"
    echo "----------------------------------------------------------------------"
}

get_uuid() {
    local dev="$1"
    blkid -s UUID -o value "$dev" 2>/dev/null || true
}

get_root_source() {
    findmnt -n -o SOURCE /
}

discover_windows_wsl_gateway_ip() {
    ip route show default | awk '{print $3; exit}'
}

print_diagnostics() {
    echo
    echo "==== Diagnostics: blkid ===="
    blkid || true
    echo
    echo "==== Diagnostics: lsblk -f ===="
    lsblk -f || true
    echo
    echo "==== Diagnostics: findmnt ===="
    findmnt || true
    echo
    echo "==== Diagnostics: df -h ===="
    df -h || true
}

ensure_dir_exists() {
    local dir="$1"
    mkdir -p "$dir"
}

is_ipv4_literal() {
    [[ "$1" =~ ^(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})(\.(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}$ ]]
}

is_clickhouse_identifier() {
    [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]
}

is_clickhouse_identifier_list() {
    local raw="$1"
    local required="${2:-true}"
    local entries entry

    if [[ -z "$raw" ]]; then
        [[ "$required" == "false" ]] && return 0
        return 1
    fi

    IFS=',' read -r -a entries <<< "$raw"
    [[ "${#entries[@]}" -gt 0 ]] || return 1

    for entry in "${entries[@]}"; do
        [[ -n "$entry" ]] || return 1
        is_clickhouse_identifier "$entry" || return 1
    done
}

is_sha256_hex() {
    [[ "$1" =~ ^[0-9A-Fa-f]{64}$ ]]
}

normalize_bool() {
    local value="$1"
    case "${value,,}" in
        1|true|yes|y|on)
            printf 'true'
            ;;
        0|false|no|n|off)
            printf 'false'
            ;;
        *)
            return 1
            ;;
    esac
}

validate_overlay_inputs() {
    [[ -n "$REPO_CLICKHOUSE_DIR" ]] || die "REPO_CLICKHOUSE_DIR is not set."
    [[ -d "$REPO_CLICKHOUSE_DIR" ]] || die "REPO_CLICKHOUSE_DIR does not exist: $REPO_CLICKHOUSE_DIR"
    [[ -d "$REPO_CLICKHOUSE_DIR/config.d" ]] || die "Repo config.d directory missing: $REPO_CLICKHOUSE_DIR/config.d"
    [[ -d "$REPO_CLICKHOUSE_DIR/users.d" ]] || die "Repo users.d directory missing: $REPO_CLICKHOUSE_DIR/users.d"
    [[ -f "$REPO_CLICKHOUSE_DIR/config.d/50-trading-keeper.xml" ]] || die "Managed Keeper config overlay is missing."

    is_ipv4_literal "$CLICKHOUSE_WORKSTATION_LAN_IP" || die "CLICKHOUSE_WORKSTATION_LAN_IP must be an IPv4 literal."
    is_ipv4_literal "$CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP" || die "CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP must be an IPv4 literal."
    is_sha256_hex "$CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX" || die "CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX must be a 64-character SHA-256 hex value."
    is_sha256_hex "$CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX" || die "CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX must be a 64-character SHA-256 hex value."
    is_ipv4_literal "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP" || die "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP must be an IPv4 literal."
    is_sha256_hex "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX" || die "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX must be a 64-character SHA-256 hex value."
    is_clickhouse_identifier_list "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES" false || die "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES must be blank or a comma-separated list of unquoted ClickHouse identifiers."
    is_clickhouse_identifier_list "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES" true || die "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES must be a comma-separated list of unquoted ClickHouse identifiers."
    CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE="$(normalize_bool "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE")" || die "CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE must be true/false, yes/no, on/off, or 1/0."
    CLICKHOUSE_FORCE_PERMISSION_REPAIR="$(normalize_bool "$CLICKHOUSE_FORCE_PERMISSION_REPAIR")" || die "CLICKHOUSE_FORCE_PERMISSION_REPAIR must be true/false, yes/no, on/off, or 1/0."
    assert_positive_integer "CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS" "$CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS"
}

render_full_access_grants() {
    printf '%s' "                <query>GRANT ALL ON *.* WITH GRANT OPTION</query>"
}

render_database_grants() {
    local read_list="$1"
    local write_list="$2"
    local allow_create_database="$3"
    local allow_file_read="$4"
    local allow_system_flush_logs="$5"
    local grants
    local read_databases write_databases database
    local has_write_access="false"

    grants="                <query>GRANT SHOW ON *.*</query>"

    if [[ -n "$read_list" ]]; then
        IFS=',' read -r -a read_databases <<< "$read_list"
        for database in "${read_databases[@]}"; do
            grants+=$'\n'"                <query>GRANT SELECT ON ${database}.*</query>"
        done
    fi

    IFS=',' read -r -a write_databases <<< "$write_list"
    for database in "${write_databases[@]}"; do
        [[ -n "$database" ]] || continue
        has_write_access="true"
        grants+=$'\n'"                <query>GRANT CREATE, DROP, ALTER, SELECT, INSERT, OPTIMIZE, TRUNCATE ON ${database}.*</query>"
    done

    if [[ "$has_write_access" == "true" ]]; then
        grants+=$'\n'"                <query>GRANT SELECT ON system.parts</query>"
    fi

    if [[ "$allow_create_database" == "true" ]]; then
        grants+=$'\n'"                <query>GRANT CREATE DATABASE ON *.*</query>"
    fi

    if [[ "$allow_file_read" == "true" ]]; then
        grants+=$'\n'"                <query>GRANT READ ON FILE *</query>"
    fi

    if [[ "$allow_system_flush_logs" == "true" ]]; then
        grants+=$'\n'"                <query>GRANT SYSTEM FLUSH LOGS ON *.*</query>"
    fi

    printf '%s' "$grants"
}

render_clickhouse_template() {
    local source_file="$1"
    local target_file="$2"
    local content

    content="$(<"$source_file")"
    content="${content//__CLICKHOUSE_WORKSTATION_LAN_IP__/$CLICKHOUSE_WORKSTATION_LAN_IP}"
    content="${content//__CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP__/$CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP}"
    content="${content//__CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX__/$CLICKHOUSE_ADMIN_PASSWORD_SHA256_HEX}"
    content="${content//__CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX__/$CLICKHOUSE_TRADING_DASHBOARD_APP_PASSWORD_SHA256_HEX}"
    content="${content//__CLICKHOUSE_TRADING_DASHBOARD_APP_GRANTS__/$(render_full_access_grants)}"
    content="${content//__CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP__/$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_IP}"
    content="${content//__CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX__/$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_PASSWORD_SHA256_HEX}"
    content="${content//__CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_GRANTS__/$(render_database_grants "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_READ_DATABASES" "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_WRITE_DATABASES" "$CLICKHOUSE_QUANT_RESEARCH_WORKBENCH_ALLOW_CREATE_DATABASE" "false" "false")}"
    printf '%s\n' "$content" > "$target_file"
}

install_clickhouse_config_overlays() {
    local config_target="/etc/clickhouse-server/config.d"
    local users_target="/etc/clickhouse-server/users.d"
    local source_file target_name target_file

    if [[ -z "$CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP" ]]; then
        CLICKHOUSE_WINDOWS_WSL_GATEWAY_IP="$(normalize_token "$(discover_windows_wsl_gateway_ip)")"
    fi

    validate_overlay_inputs

    install -d -o clickhouse -g clickhouse -m 750 "$config_target"
    install -d -o clickhouse -g clickhouse -m 750 "$users_target"

    rm -f "$config_target"/*trading-dashboard*.xml
    rm -f "$users_target"/*trading-dashboard*.xml

    shopt -s nullglob
    for source_file in "$REPO_CLICKHOUSE_DIR/config.d"/*.xml; do
        target_file="$config_target/$(basename "$source_file")"
        install -o clickhouse -g clickhouse -m 400 "$source_file" "$target_file"
    done

    for source_file in "$REPO_CLICKHOUSE_DIR/config.d"/*.xml.template; do
        target_name="$(basename "$source_file" .template)"
        target_file="$config_target/$target_name"
        render_clickhouse_template "$source_file" "$target_file"
        chown clickhouse:clickhouse "$target_file"
        chmod 400 "$target_file"
    done

    for source_file in "$REPO_CLICKHOUSE_DIR/users.d"/*.xml; do
        target_file="$users_target/$(basename "$source_file")"
        install -o clickhouse -g clickhouse -m 400 "$source_file" "$target_file"
    done

    for source_file in "$REPO_CLICKHOUSE_DIR/users.d"/*.xml.template; do
        target_name="$(basename "$source_file" .template)"
        target_file="$users_target/$target_name"
        render_clickhouse_template "$source_file" "$target_file"
        chown clickhouse:clickhouse "$target_file"
        chmod 400 "$target_file"
    done
    shopt -u nullglob
}

stop_clickhouse_if_running() {
    if ! clickhouse_process_is_running; then
        return 0
    fi

    echo "Stopping existing ClickHouse server..."
    pkill -TERM -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" || true

    for _ in $(seq 1 30); do
        if ! clickhouse_process_is_running; then
            echo "ClickHouse stopped."
            return 0
        fi
        sleep 1
    done

    echo "ClickHouse did not stop cleanly."
    print_clickhouse_processes
    die "Refusing to continue while ClickHouse is still running."
}

stop_script_started_clickhouse_after_startup_failure() {
    if ! clickhouse_process_is_running; then
        return 0
    fi

    echo
    echo "Stopping ClickHouse process started by this failed bootstrap attempt..."
    pkill -TERM -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" || true

    for _ in $(seq 1 30); do
        if ! clickhouse_process_is_running; then
            echo "ClickHouse stopped after startup failure."
            return 0
        fi
        sleep 1
    done

    echo "ClickHouse did not stop after startup failure."
    print_clickhouse_processes
}

discover_candidate_partitions() {
    local name type fstype mp

    while read -r name type fstype mp; do
        [[ "$type" != "part" ]] && continue
        [[ "$fstype" != "ext4" ]] && continue
        [[ "$mp" == "/" ]] && continue
        echo "$name"
    done < <(lsblk -lnpo NAME,TYPE,FSTYPE,MOUNTPOINT)
}

assert_expected_uuid_matches() {
    local dev="$1"
    local expected_uuid="${2:-$EXPECTED_DATA_FS_UUID}"
    local uuid
    uuid="$(normalize_token "$(get_uuid "$dev")")"

    [[ -n "$uuid" ]] || die "No UUID found for candidate device: $dev"

    echo "Candidate UUID: $uuid"

    [[ "$uuid" == "$expected_uuid" ]] || {
        echo "Expected UUID:  $expected_uuid"
        echo "Candidate UUID: $uuid"
        die "Candidate device UUID does not match expected data filesystem UUID."
    }
}

find_ext4_partition_by_uuid() {
    local expected_uuid="$1"
    local label="$2"
    local dev uuid matched_device

    matched_device=""
    for dev in "${CANDIDATES[@]}"; do
        uuid="$(normalize_token "$(get_uuid "$dev")")"
        if [[ -n "$uuid" && "$uuid" == "$expected_uuid" ]]; then
            [[ -z "$matched_device" ]] || die "More than one candidate matched $label UUID."
            matched_device="$dev"
        fi
    done

    [[ -n "$matched_device" ]] || {
        print_diagnostics
        die "No candidate partition matched $label UUID: $expected_uuid"
    }

    printf '%s' "$matched_device"
}

ensure_mount_is_canonical() {
    local dev="$1"
    local mount_point="$2"

    if mountpoint -q "$mount_point"; then
        local current_source
        current_source="$(findmnt -n -o SOURCE "$mount_point" || true)"

        if [[ "$current_source" != "$dev" ]]; then
            echo "Unmounting unexpected existing $mount_point mount from: $current_source"
            umount "$mount_point"
            mount "$dev" "$mount_point"
        fi
    else
        mount "$dev" "$mount_point"
    fi
}

ensure_data_mount_is_canonical() {
    ensure_mount_is_canonical "$1" "$DATA_MOUNT"
}


ensure_clickhouse_bind_mount_is_canonical() {
    if mountpoint -q "$CH_TARGET"; then
        local current_source
        current_source="$(findmnt -n -o SOURCE "$CH_TARGET" || true)"
        echo "Unmounting existing /var/lib/clickhouse mount from: $current_source"
        umount "$CH_TARGET"
    fi

    mount --bind "$CH_DIR_ON_DATA" "$CH_TARGET"
}

validate_mount_chain() {
    local data_source ch_source actual_uuid expected_ch_source
    local inode_data inode_target

    echo "Mount state:"
    findmnt "$DATA_MOUNT"
    findmnt "$CH_TARGET"

    data_source="$(findmnt -n -o SOURCE "$DATA_MOUNT")"
    ch_source="$(findmnt -n -o SOURCE "$CH_TARGET")"
    actual_uuid="$(normalize_token "$(get_uuid "$data_source")")"

    [[ "$data_source" == "$MATCHED_DEVICE" ]] || die "$DATA_MOUNT is not mounted from expected device."
    [[ "$actual_uuid" == "$EXPECTED_DATA_FS_UUID" ]] || die "Mounted /data device UUID mismatch."

    # --------------------------------------------------------------------------
    # /data/clickhouse is just a directory under the filesystem mounted at /data.
    # /var/lib/clickhouse should therefore appear as the same filesystem source,
    # but with the /clickhouse subdirectory encoded in the source string.
    # --------------------------------------------------------------------------
    expected_ch_source="${MATCHED_DEVICE}[/clickhouse]"

    [[ "$ch_source" == "$expected_ch_source" ]] || {
        echo "Expected ClickHouse mount source: $expected_ch_source"
        echo "Actual ClickHouse mount source:   $ch_source"
        die "$CH_TARGET does not point to the expected subdirectory on the data disk."
    }

    [[ -f "$CH_DIR_ON_DATA/$SENTINEL_FILE" ]] || die "Sentinel file missing on data disk."
    [[ -f "$CH_TARGET/$SENTINEL_FILE" ]] || die "Sentinel file missing under ClickHouse target."

    # --------------------------------------------------------------------------
    # Strong sanity check: both paths must reference the exact same file.
    # If device:inode matches, the mount chain is functionally correct.
    # --------------------------------------------------------------------------
    inode_data="$(stat -c '%d:%i' "$CH_DIR_ON_DATA/$SENTINEL_FILE")"
    inode_target="$(stat -c '%d:%i' "$CH_TARGET/$SENTINEL_FILE")"

    [[ "$inode_data" == "$inode_target" ]] || {
        echo "Sentinel inode on $CH_DIR_ON_DATA : $inode_data"
        echo "Sentinel inode on $CH_TARGET      : $inode_target"
        die "Canonical data dir and ClickHouse target do not reference the same underlying file."
    }
}

validate_clickhouse_storage_mount() {
    local mount_point="$1"
    local expected_device="$2"
    local expected_uuid="$3"
    local label="$4"
    local data_source actual_uuid

    echo "$label mount state:"
    findmnt "$mount_point"

    data_source="$(findmnt -n -o SOURCE "$mount_point")"
    actual_uuid="$(normalize_token "$(get_uuid "$data_source")")"

    [[ "$data_source" == "$expected_device" ]] || die "$mount_point is not mounted from expected $label device."
    [[ "$actual_uuid" == "$expected_uuid" ]] || die "$label mounted device UUID mismatch."
    [[ -f "$mount_point/$SENTINEL_FILE" ]] || die "Sentinel file missing on $label mount."
}

ensure_clickhouse_storage_directory() {
    local mount_point="$1"
    local data_dir="$2"
    local label="$3"

    ensure_dir_exists "$data_dir"
    if [[ ! -f "$mount_point/$SENTINEL_FILE" ]]; then
        echo "Creating $label sentinel file..."
        printf 'This directory is the intended %s ClickHouse disk mount.\n' "$label" > "$mount_point/$SENTINEL_FILE"
    fi
}

validate_permissions_and_dependencies() {
    sudo -u clickhouse stat -f "$CH_TARGET" >/dev/null || die "ClickHouse user cannot stat target directory."
    sudo -u clickhouse test -r "$CH_TARGET/$SENTINEL_FILE" || die "ClickHouse user cannot read sentinel file."
    sudo -u clickhouse stat -f "$SIP_RAW_SSD_CLICKHOUSE_DIR" >/dev/null || die "ClickHouse user cannot stat SIP raw SSD directory."
    sudo -u clickhouse stat -f "$LIVE_MARKET_SSD_CLICKHOUSE_DIR" >/dev/null || die "ClickHouse user cannot stat live market SSD directory."

    [[ -d "$USER_FILES_PATH" ]] || die "Configured user_files_path does not exist: $USER_FILES_PATH"
}

clickhouse_storage_permissions_are_usable() {
    local probe_file="$CH_TARGET/.clickhouse_permission_probe.$$"

    [[ "$(stat -c '%U:%G' "$CH_DIR_ON_DATA")" == "clickhouse:clickhouse" ]] || return 1
    sudo -u clickhouse stat -f "$CH_TARGET" >/dev/null || return 1
    sudo -u clickhouse test -r "$CH_TARGET/$SENTINEL_FILE" || return 1
    sudo -u clickhouse test -w "$CH_TARGET" || return 1
    sudo -u clickhouse sh -c 'umask 077; : > "$1"; rm -f "$1"' sh "$probe_file" || return 1
}

clickhouse_disk_permissions_are_usable() {
    local mount_point="$1"
    local data_dir="$2"
    local probe_file="$data_dir/.clickhouse_permission_probe.$$"

    [[ "$(stat -c '%U:%G' "$data_dir")" == "clickhouse:clickhouse" ]] || return 1
    sudo -u clickhouse stat -f "$data_dir" >/dev/null || return 1
    sudo -u clickhouse test -r "$mount_point/$SENTINEL_FILE" || return 1
    sudo -u clickhouse test -w "$data_dir" || return 1
    sudo -u clickhouse sh -c 'umask 077; : > "$1"; rm -f "$1"' sh "$probe_file" || return 1
}

repair_clickhouse_disk_permissions() {
    local mount_point="$1"
    local data_dir="$2"
    local label="$3"

    if [[ "$CLICKHOUSE_FORCE_PERMISSION_REPAIR" != "true" ]] && clickhouse_disk_permissions_are_usable "$mount_point" "$data_dir"; then
        echo "$label storage permissions are already usable; skipping recursive chown."
        chmod 750 "$mount_point" "$data_dir"
        return 0
    fi

    if [[ "$CLICKHOUSE_FORCE_PERMISSION_REPAIR" == "true" ]]; then
        echo "CLICKHOUSE_FORCE_PERMISSION_REPAIR=true; repairing $label ownership."
    else
        echo "ClickHouse user cannot fully use $label storage; repairing ownership."
    fi

    chown -R clickhouse:clickhouse "$mount_point"
    chmod 750 "$mount_point" "$data_dir"
}

repair_clickhouse_storage_permissions() {
    chmod 755 "$DATA_MOUNT"

    if [[ "$CLICKHOUSE_FORCE_PERMISSION_REPAIR" != "true" ]] && clickhouse_storage_permissions_are_usable; then
        echo "ClickHouse storage permissions are already usable; skipping recursive chown."
        chmod 750 "$CH_DIR_ON_DATA"
        return 0
    fi

    if [[ "$CLICKHOUSE_FORCE_PERMISSION_REPAIR" == "true" ]]; then
        echo "CLICKHOUSE_FORCE_PERMISSION_REPAIR=true; running recursive ownership repair."
    else
        echo "ClickHouse user cannot fully use storage; running recursive ownership repair."
    fi

    chown -R clickhouse:clickhouse "$CH_DIR_ON_DATA"
    chmod 750 "$CH_DIR_ON_DATA"
}

clickhouse_process_is_running() {
    pgrep -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" >/dev/null
}

keeper_is_ready() {
    local reply
    reply="$(timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/9181; printf ruok >&3; head -c 4 <&3' 2>/dev/null || true)"
    [[ "$reply" == "imok" ]]
}

print_clickhouse_processes() {
    pgrep -a -u clickhouse -f "$CLICKHOUSE_PROCESS_PATTERN" || true
}

disable_clickhouse_systemd_unit() {
    # The package unit starts before Windows can attach the external disks.
    # Disable it persistently so only this mount-aware bootstrap can start the
    # server after the required HDD and SSD paths have been validated.
    if [[ ! -d /run/systemd/system ]] || ! command -v systemctl >/dev/null; then
        echo "systemd is not active; no ClickHouse package unit needs coordination."
        return 0
    fi

    echo "Disabling the competing ClickHouse systemd unit..."
    systemctl disable clickhouse-server.service >/dev/null 2>&1 || true
    systemctl stop clickhouse-server.service >/dev/null 2>&1 || true
    systemctl reset-failed clickhouse-server.service >/dev/null 2>&1 || true
}

start_clickhouse_and_wait() {
    if clickhouse_process_is_running; then
        echo "ClickHouse is already running before script-managed startup."
        print_clickhouse_processes
        die "Refusing to continue because ClickHouse should have been stopped before the bind mount was finalized."
    fi

    cd /
    nohup sudo -u clickhouse "$CLICKHOUSE_BIN" server --config-file="$CLICKHOUSE_CONFIG" \
        >"$RUNTIME_LOG_DIR/server-console.log" 2>&1 &

    echo "Waiting up to ${CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS}s for ClickHouse process and network readiness..."
    local ready=0

    for _ in $(seq 1 "$CLICKHOUSE_STARTUP_READY_TIMEOUT_SECONDS"); do
        if clickhouse_process_is_running; then
            if ss -ltn | awk '{print $4}' | grep -Eq '(:9000|:8123)$' \
                && ss -ltn | awk '{print $4}' | grep -Eq ':9181$' \
                && keeper_is_ready; then
                ready=1
                break
            fi
        fi
        sleep 1
    done

    if [[ "$ready" -ne 1 ]]; then
        echo "ClickHouse failed to become ready."
        echo
        echo "==== process list ===="
        print_clickhouse_processes
        echo
        echo "==== listening ports ===="
        ss -ltnp | grep -E '9000|8123|9181' || true
        echo
        echo "==== $RUNTIME_LOG_DIR/server-console.log ===="
        cat "$RUNTIME_LOG_DIR/server-console.log" || true
        echo
        echo "==== /var/log/clickhouse-server/clickhouse-server.log ===="
        tail -n 200 /var/log/clickhouse-server/clickhouse-server.log || true
        echo
        echo "==== /var/log/clickhouse-server/clickhouse-server.err.log ===="
        tail -n 200 /var/log/clickhouse-server/clickhouse-server.err.log || true
        stop_script_started_clickhouse_after_startup_failure
        exit 1
    fi

    echo "ClickHouse and local Keeper ports are ready; large tables may continue loading asynchronously."
}

post_start_validation() {
    echo
    echo "ClickHouse process:"
    print_clickhouse_processes

    echo
    echo "Listening ports:"
    ss -ltnp | grep -E '9000|8123|9181' || true

    echo
    echo "Mounts:"
    findmnt /data
    findmnt /var/lib/clickhouse
    findmnt "$SIP_RAW_SSD_MOUNT"
    findmnt "$LIVE_MARKET_SSD_MOUNT"
}
# ------------------------------------------------------------------------------
# Begin
# ------------------------------------------------------------------------------
[[ -n "$EXPECTED_DATA_FS_UUID" ]] || die "EXPECTED_DATA_FS_UUID is not set."
[[ -n "$EXPECTED_SIP_RAW_SSD_FS_UUID" ]] || die "EXPECTED_SIP_RAW_SSD_FS_UUID is not set."
[[ -n "$EXPECTED_LIVE_MARKET_SSD_FS_UUID" ]] || die "EXPECTED_LIVE_MARKET_SSD_FS_UUID is not set."

log_section "[1/14] Discover candidate ext4 partitions"
mapfile -t CANDIDATES < <(discover_candidate_partitions)

if [[ "${#CANDIDATES[@]}" -eq 0 ]]; then
    print_diagnostics
    die "No candidate ext4 partition found."
fi

printf 'Candidates:\n'
printf '  %s\n' "${CANDIDATES[@]}"

log_section "[2/14] Match candidate partitions against expected UUIDs"
MATCHED_DEVICE=""
MATCHED_SIP_RAW_SSD_DEVICE=""
MATCHED_LIVE_MARKET_SSD_DEVICE=""

for dev in "${CANDIDATES[@]}"; do
    uuid="$(normalize_token "$(get_uuid "$dev")")"
    echo "  Candidate: $dev  UUID: ${uuid:-<none>}"
done

MATCHED_DEVICE="$(find_ext4_partition_by_uuid "$EXPECTED_DATA_FS_UUID" "primary data HDD")"
MATCHED_SIP_RAW_SSD_DEVICE="$(find_ext4_partition_by_uuid "$EXPECTED_SIP_RAW_SSD_FS_UUID" "SIP raw SSD")"
MATCHED_LIVE_MARKET_SSD_DEVICE="$(find_ext4_partition_by_uuid "$EXPECTED_LIVE_MARKET_SSD_FS_UUID" "live market SSD")"

echo "Matched data device: $MATCHED_DEVICE"
echo "Matched SIP raw SSD device: $MATCHED_SIP_RAW_SSD_DEVICE"
echo "Matched live market SSD device: $MATCHED_LIVE_MARKET_SSD_DEVICE"
assert_expected_uuid_matches "$MATCHED_DEVICE" "$EXPECTED_DATA_FS_UUID"
assert_expected_uuid_matches "$MATCHED_SIP_RAW_SSD_DEVICE" "$EXPECTED_SIP_RAW_SSD_FS_UUID"
assert_expected_uuid_matches "$MATCHED_LIVE_MARKET_SSD_DEVICE" "$EXPECTED_LIVE_MARKET_SSD_FS_UUID"

log_section "[3/14] Ensure mount points exist"
ensure_dir_exists "$DATA_MOUNT"
ensure_dir_exists "$CH_TARGET"
ensure_dir_exists "$SIP_RAW_SSD_MOUNT"
ensure_dir_exists "$LIVE_MARKET_SSD_MOUNT"

log_section "[4/14] Disable systemd ownership and stop ClickHouse if already running"
disable_clickhouse_systemd_unit
stop_clickhouse_if_running

log_section "[5/14] Ensure canonical /data mount"
ensure_data_mount_is_canonical "$MATCHED_DEVICE"

log_section "[6/14] Ensure canonical SSD mounts"
ensure_mount_is_canonical "$MATCHED_SIP_RAW_SSD_DEVICE" "$SIP_RAW_SSD_MOUNT"
ensure_mount_is_canonical "$MATCHED_LIVE_MARKET_SSD_DEVICE" "$LIVE_MARKET_SSD_MOUNT"

log_section "[7/14] Ensure canonical ClickHouse directories on mounted disks"
ensure_dir_exists "$CH_DIR_ON_DATA"
ensure_clickhouse_storage_directory "$SIP_RAW_SSD_MOUNT" "$SIP_RAW_SSD_CLICKHOUSE_DIR" "SIP raw SSD"
ensure_clickhouse_storage_directory "$LIVE_MARKET_SSD_MOUNT" "$LIVE_MARKET_SSD_CLICKHOUSE_DIR" "live market SSD"
install -d -o clickhouse -g clickhouse -m 700 "$LIVE_MARKET_SSD_MOUNT/keeper"
install -d -o clickhouse -g clickhouse -m 700 "$LIVE_MARKET_SSD_MOUNT/keeper/log"
install -d -o clickhouse -g clickhouse -m 700 "$LIVE_MARKET_SSD_MOUNT/keeper/snapshots"

if [[ ! -f "$CH_DIR_ON_DATA/$SENTINEL_FILE" ]]; then
    echo "Creating sentinel file..."
    printf 'This directory is the intended ClickHouse home on the mounted big disk.\n' > "$CH_DIR_ON_DATA/$SENTINEL_FILE"
fi

log_section "[8/14] Force canonical bind mount for /var/lib/clickhouse"
ensure_clickhouse_bind_mount_is_canonical

log_section "[9/14] Fix permissions"
repair_clickhouse_storage_permissions
repair_clickhouse_disk_permissions "$SIP_RAW_SSD_MOUNT" "$SIP_RAW_SSD_CLICKHOUSE_DIR" "SIP raw SSD"
repair_clickhouse_disk_permissions "$LIVE_MARKET_SSD_MOUNT" "$LIVE_MARKET_SSD_CLICKHOUSE_DIR" "live market SSD"

log_section "[10/14] Validate mount chain"
validate_mount_chain
validate_clickhouse_storage_mount "$SIP_RAW_SSD_MOUNT" "$MATCHED_SIP_RAW_SSD_DEVICE" "$EXPECTED_SIP_RAW_SSD_FS_UUID" "SIP raw SSD"
validate_clickhouse_storage_mount "$LIVE_MARKET_SSD_MOUNT" "$MATCHED_LIVE_MARKET_SSD_DEVICE" "$EXPECTED_LIVE_MARKET_SSD_FS_UUID" "live market SSD"

log_section "[11/14] Validate permissions and external dependencies"
validate_permissions_and_dependencies

log_section "[12/14] Install ClickHouse config overlays"
install_clickhouse_config_overlays

log_section "[13/14] Start ClickHouse and validate"
start_clickhouse_and_wait

log_section "[14/14] Post-start validation"
post_start_validation

echo
echo "======================================================================"
echo "ClickHouse bootstrap completed successfully."
echo "Data mount source:       $(findmnt -n -o SOURCE "$DATA_MOUNT")"
echo "ClickHouse bind source:  $(findmnt -n -o SOURCE "$CH_TARGET")"
echo "SIP raw SSD source:      $(findmnt -n -o SOURCE "$SIP_RAW_SSD_MOUNT")"
echo "Live market SSD source:  $(findmnt -n -o SOURCE "$LIVE_MARKET_SSD_MOUNT")"
echo "======================================================================"

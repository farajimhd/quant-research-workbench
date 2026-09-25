"""Provision the dedicated ARTE journal writer on the workstation.

This operator command changes ClickHouse access control, not market tables. It
never prints or stores the generated password outside the workstation secrets
directory. Re-running it reconciles grants only after authenticating with the
already saved credential; it never rotates an existing account implicitly.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.mlops.clickhouse import ClickHouseHttpClient
from research.mlops.env import load_env_file
from src.trading_runtime.arte_journal_schema import (
    MARKET_READ_TABLES, TABLES, journal_permission_preflight, storage_preflight,
)
from src.backend.live_signal_journal_preflight import (
    staged_grants, staged_live_signal_storage_preflight,
)


PRINCIPAL = "trading_journal_writer"
SECRET_ROOT = Path(r"D:\TradingML\secrets")
SECRET_PATH = SECRET_ROOT / "trading_journal.env"
SETTINGS_PATH = SECRET_ROOT / ".env"
SYSTEM_READ_TABLES = ("storage_policies", "tables", "columns", "parts",
                      "data_skipping_indices")


def _restrict_secret_file(path: Path) -> None:
    """Remove inherited Windows grants before a password is written."""
    literal = str(path).replace("'", "''")
    command = f"""
$ErrorActionPreference = 'Stop'
$file = '{literal}'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
$system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$allowed = @($owner, $system, $admins)
function Assert-PrivateAcl($acl, $allowed) {{
    if (-not $acl.AreAccessRulesProtected) {{ throw 'Secret ACL still inherits permissions' }}
    $seen = @{{}}
    foreach ($rule in $acl.Access) {{
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier])
        if ($rule.AccessControlType -ne 'Allow' -or $rule.IsInherited -or
            -not ($allowed | Where-Object {{ $_.Value -eq $sid.Value }}) -or
            $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {{
            throw 'Secret ACL contains an unapproved or insufficient rule'
        }}
        $seen[$sid.Value] = $true
    }}
    foreach ($sid in $allowed) {{
        if (-not $seen.ContainsKey($sid.Value)) {{ throw 'Secret ACL lacks a required owner' }}
    }}
}}
$acl = Get-Acl -LiteralPath $file
if ($acl.AreAccessRulesProtected) {{
    Assert-PrivateAcl $acl $allowed
    return
}}
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in $allowed) {{
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
}}
Set-Acl -LiteralPath $file -AclObject $acl
$check = Get-Acl -LiteralPath $file
Assert-PrivateAcl $check $allowed
"""
    shell_env = dict(os.environ)
    shell_env["PSModulePath"] = os.pathsep.join((
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
            "WindowsPowerShell" / "Modules"),
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
            "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
    ))
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False, capture_output=True, text=True, env=shell_env,
    )
    if result.returncode:
        raise RuntimeError("Could not establish a private ACL on the journal credential file")


def _credential(path: Path, *, account_exists: bool) -> str:
    if path.exists():
        _restrict_secret_file(path)
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        if values.get("TRADING_JOURNAL_CLICKHOUSE_USER") != PRINCIPAL:
            if not values and not account_exists:
                return _write_credential(path)
            raise RuntimeError("Existing journal secret belongs to a different principal")
        password = values.get("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "")
        if len(password) < 40:
            raise RuntimeError("Existing journal secret is incomplete; refusing to rotate it")
        return password

    # The file is empty while its ACL is set. It contains no secret at any
    # point when inherited Users read permission could still apply.
    with path.open("x", encoding="utf-8"):
        pass
    _restrict_secret_file(path)
    return _write_credential(path)


def _write_credential(path: Path) -> str:
    password = secrets.token_urlsafe(48)
    path.write_text(
        "TRADING_JOURNAL_CLICKHOUSE_URL=http://192.168.0.21:18123\n"
        f"TRADING_JOURNAL_CLICKHOUSE_USER={PRINCIPAL}\n"
        f"TRADING_JOURNAL_CLICKHOUSE_PASSWORD={password}\n",
        encoding="utf-8",
    )
    _restrict_secret_file(path)
    return password


def _grants(*, staged_live_signal: bool = False) -> tuple[str, ...]:
    statements = [
        f"GRANT SELECT, INSERT ON arte.{table.name} TO {PRINCIPAL}"
        for table in TABLES
    ]
    statements.extend(
        f"GRANT SELECT ON arte.{name} TO {PRINCIPAL}"
        for name in sorted(MARKET_READ_TABLES)
    )
    statements.extend(
        f"GRANT SELECT ON system.{name} TO {PRINCIPAL}"
        for name in SYSTEM_READ_TABLES
    )
    if staged_live_signal:
        statements.extend(staged_grants(PRINCIPAL))
    return tuple(statements)


def _admin_client(url: str) -> ClickHouseHttpClient:
    if not SETTINGS_PATH.is_file():
        raise RuntimeError("Workstation ClickHouse settings file is missing")
    load_env_file(SETTINGS_PATH)
    user = os.environ.get("CLICKHOUSE_WORKSTATION_USER", "")
    password = os.environ.get("CLICKHOUSE_WORKSTATION_PASSWORD", "")
    if not user or not password or user == PRINCIPAL:
        raise RuntimeError("Distinct ClickHouse administrator credentials are required")
    return ClickHouseHttpClient(url, user, password, timeout_seconds=20)


def provision(url: str, *, apply: bool, staged_live_signal: bool = False) -> None:
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Provisioning must run on DESKTOP-SAAI85T")
    if not SECRET_ROOT.is_dir():
        raise RuntimeError("Required workstation secrets directory is unavailable")
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.hostname, parsed.port, parsed.path, parsed.query,
            parsed.fragment, parsed.username, parsed.password) != (
                "http", "desktop-saai85t", 18123, "", "", "", None, None):
        raise RuntimeError("Unexpected ClickHouse endpoint; refusing credential creation")
    client = _admin_client(url)
    present = client.execute(
        f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}' FORMAT TabSeparated"
    ).strip()
    if present not in {"0", "1"}:
        raise RuntimeError("ClickHouse principal inventory is inconsistent")
    print(f"Journal principal: {'present' if present == '1' else 'absent'}")
    grants = _grants(staged_live_signal=staged_live_signal)
    print(f"Required grants: {len(grants)} exact table grants")
    if not apply:
        print("Plan only; no credential or ClickHouse state changed")
        return

    if staged_live_signal:
        staged_live_signal_storage_preflight(client)

    password = _credential(SECRET_PATH, account_exists=present == "1")
    writer = ClickHouseHttpClient(url, PRINCIPAL, password, timeout_seconds=20)
    if present == "1":
        try:
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL:
                raise RuntimeError("Journal credential authenticated as the wrong user")
        except Exception as exc:
            raise RuntimeError("Existing journal account does not match saved credential") from exc
    else:
        digest = sha256(password.encode("utf-8")).hexdigest()
        client.execute(
            f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
            "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'"
        )
    for statement in _grants(staged_live_signal=staged_live_signal):
        client.execute(statement)
    storage_preflight(writer)
    journal_permission_preflight(writer)
    print("Journal principal authenticated; SSD placement, exact grants, and market-write denial verified")
    print(f"Credential stored with a private ACL: {SECRET_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a least-privilege ARTE journal writer")
    parser.add_argument("--url", default="http://DESKTOP-SAAI85T:18123",
                        help="managed workstation ClickHouse endpoint")
    parser.add_argument("--apply", action="store_true", help="create credential and ClickHouse grants")
    parser.add_argument("--staged-live-signal", action="store_true",
                        help="also grant preprovisioned typed dispatch/completion tables")
    args = parser.parse_args()
    try:
        provision(args.url, apply=args.apply,
                  staged_live_signal=args.staged_live_signal)
    except Exception as exc:
        print(f"Journal provisioning failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

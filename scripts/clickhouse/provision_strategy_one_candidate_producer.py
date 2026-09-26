"""Provision a narrow writer for the producer-owned Strategy 1 candidates.

Dry-run by default. The credential stays on the managed workstation; this
principal has no INSERT authority on bars, indicators, liquidity, or journals.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import sys
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, _admin_client, _restrict_secret_file,
)
from src.trading_runtime.strategy_one_candidate_schema import (
    CANDIDATE_TABLE, COVERAGE_TABLE, verify_tables,
)


URL = "http://DESKTOP-SAAI85T:18123"
WORKSTATION_IPV4 = "192.168.1.218"
PRINCIPAL = "strategy_one_candidate_producer"
SECRET_PATH = SECRET_ROOT / "strategy_one_candidate_producer.env"
_TABLES = (CANDIDATE_TABLE, COVERAGE_TABLE)
_GRANTS = frozenset((privilege, table) for privilege in ("SELECT", "INSERT")
                    for table in _TABLES)
_GRANT_PATTERN = re.compile(
    rf"GRANT ([A-Z ,]+) ON (arte\.[A-Za-z_][A-Za-z0-9_]*) TO {PRINCIPAL}\Z")


def _credential(*, account_exists: bool) -> str:
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        values = dict(line.split("=", 1) for line in
                      SECRET_PATH.read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        password = values.get("STRATEGY_ONE_CANDIDATE_PASSWORD", "")
        if values:
            if (values.get("STRATEGY_ONE_CANDIDATE_URL") != URL
                    or values.get("STRATEGY_ONE_CANDIDATE_USER") != PRINCIPAL
                    or len(password) < 40):
                raise RuntimeError("Existing candidate credential is incomplete")
            return password
        if account_exists:
            raise RuntimeError("Existing candidate principal lacks private credential")
        # An ACL failure before the first write may leave our empty file.
        # Retain its verified private ACL and finish this new-account setup.
        if SECRET_PATH.stat().st_size != 0:
            raise RuntimeError("Candidate credential file is malformed")
    else:
        if account_exists:
            raise RuntimeError("Existing candidate principal lacks private credential")
        with SECRET_PATH.open("x", encoding="utf-8"):
            pass
        _restrict_secret_file(SECRET_PATH)
    password = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(
        f"STRATEGY_ONE_CANDIDATE_URL={URL}\n"
        f"STRATEGY_ONE_CANDIDATE_USER={PRINCIPAL}\n"
        f"STRATEGY_ONE_CANDIDATE_PASSWORD={password}\n", encoding="utf-8")
    _restrict_secret_file(SECRET_PATH)
    return password


def _grant_set(client) -> frozenset[tuple[str, str]]:
    grants: set[tuple[str, str]] = set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = _GRANT_PATTERN.fullmatch(line.strip())
        if match is None:
            raise RuntimeError("Candidate producer has broad or unrecognized grants")
        for privilege in (value.strip() for value in match.group(1).split(",")):
            if privilege not in {"SELECT", "INSERT"}:
                raise RuntimeError("Candidate producer has unauthorized privilege")
            grants.add((privilege, match.group(2)))
    if not grants <= _GRANTS:
        raise RuntimeError("Candidate producer has grants outside its two tables")
    return frozenset(grants)


def provision(admin, *, credential, client_factory) -> None:
    """Reconcile exact table grants without rotating an existing identity."""
    verify_tables(admin)
    present = admin.execute(
        "SELECT count() FROM system.users "
        f"WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {"0", "1"}:
        raise RuntimeError("Candidate producer account inventory is inconsistent")
    password = credential(account_exists=present == "1")
    if not isinstance(password, str) or len(password) < 40:
        raise RuntimeError("Candidate producer password is incomplete")
    if present == "0":
        digest = sha256(password.encode()).hexdigest()
        admin.execute(
            f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
            "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
    client = client_factory(PRINCIPAL, password)
    try:
        if client.execute("SELECT currentUser()").strip() != PRINCIPAL:
            raise RuntimeError("Candidate credential authenticates as another user")
        existing = _grant_set(client)
        for privilege, table in sorted(_GRANTS - existing):
            admin.execute(f"GRANT {privilege} ON {table} TO {PRINCIPAL}")
        if _grant_set(client) != _GRANTS:
            raise RuntimeError("Candidate producer grants did not reconcile")
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="create/reconcile the dedicated producer principal")
    parser.add_argument("--confirm-candidate-producer", action="store_true",
                        help="required second confirmation for --apply")
    args = parser.parse_args(argv)
    if not args.apply:
        print(f"DRY RUN: {PRINCIPAL}; SELECT and INSERT on two candidate tables only.")
        print("No connection, credential, account, or grant change was made.")
        print("Apply on DESKTOP-SAAI85T with --apply --confirm-candidate-producer.")
        return 0
    if not args.confirm_candidate_producer:
        parser.error("--apply requires --confirm-candidate-producer")
    if platform.node().upper() != "DESKTOP-SAAI85T" or not SECRET_ROOT.is_dir():
        print("Blocked: managed workstation/private secrets directory required.",
              file=sys.stderr)
        return 1
    admin = None
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(
            "DESKTOP-SAAI85T", 18123, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is unavailable")
        endpoint = f"http://{WORKSTATION_IPV4}:18123"
        admin = _admin_client(endpoint)
        provision(admin, credential=_credential,
                  client_factory=lambda user, password: ClickHouseHttpClient(
                      endpoint, user, password, timeout_seconds=20))
    except Exception as exc:
        # Driver errors can contain SQL or credentials; do not echo them.
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{frame.name}:{frame.lineno}" for frame in reversed(frames)
                      if frame.filename == __file__), "external_dependency")
        print(f"Candidate producer provisioning stopped: {type(exc).__name__} "
              f"at {stage}; "
              "partial account/grants may exist. Inspect private diagnostics.",
              file=sys.stderr)
        return 1
    finally:
        if admin is not None:
            admin.close()
    print("Candidate producer authenticated with exactly two SELECT/INSERT table grants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

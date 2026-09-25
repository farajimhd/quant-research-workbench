"""Producer-only publication of one archived market-day build certificate.

Dry run by default. --apply is an explicit operator action on the workstation;
Backtest never invokes this command and never receives its write credentials.
All stored families are normalized typed arte tables, with no JSON columns.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import platform
import re
import socket
import sys
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.plan_market_day_certificate import (
    DEFAULT_RUNTIME, prepare_saved_build,
)
from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import (
    MarketDayKeeperAuthority, MarketDayKeeperReader,
)
from src.trading_runtime.arte_market_day_certification import TABLES
from src.trading_runtime.arte_market_day_publisher import (
    MarketDayCertificateClient, _exact, publish_market_day_certificate,
)
from src.trading_runtime.keeper_session import open_workstation_keeper_session


class CanonicalSourceReader:
    """Expose only producer source SELECTs to the V5 plan verifier.

    The certificate HTTP client has ``execute``; V5's source-plan contract
    expects ``query`` returning dictionaries. No DDL or market mutation can
    pass through this adapter during a certificate publication.
    """

    def __init__(self, http) -> None:
        self.http = http

    def query(self, sql: str, label: str = "source_plan", read: bool = True) -> list[dict]:
        if (not read or not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE)
                or re.search(r"\b(INSERT|ALTER|CREATE|DROP|TRUNCATE|OPTIMIZE|SYSTEM|KILL)\b",
                             sql, re.IGNORECASE)
                or re.search(r"\bFORMAT\b", sql, re.IGNORECASE)):
            raise ValueError(f"Canonical source {label} must be one SELECT without FORMAT")
        return [json.loads(line) for line in
                self.http.execute(sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _certificate_admin_client(url: str):
    # Whole-build canonical parity and exact typed-family readback can exceed
    # the short timeout used by credential provisioning. This is control-plane
    # work, never a Backtest or live market callback.
    client = _admin_client(url)
    client.timeout_seconds = 180
    return client


def _workstation_clickhouse_url() -> str:
    # Windows may resolve this machine's name to a link-local IPv6 address
    # even though the managed WSL ClickHouse port is exposed only over IPv4.
    # Resolve at launch rather than pinning a potentially changing LAN address.
    address = IPv4Address(socket.gethostbyname("DESKTOP-SAAI85T"))
    if not address.is_private:
        raise RuntimeError("Workstation ClickHouse resolved outside the private network")
    return f"http://{address}:18123"


def publish_saved_build(runtime: Path, build_id: str, *, apply: bool,
                        client_factory=_certificate_admin_client,
                        keeper_session_factory=open_workstation_keeper_session) -> dict:
    """Prepare exact archive rows; publish only under an explicit producer call."""
    prepared, sessions = prepare_saved_build(runtime, build_id)
    counts = {name: len(rows) for name, rows in prepared.items()}
    if not apply:
        return {"build_id": build_id, "sessions": sessions,
                "family_rows": counts, "status": "plan_only"}
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Market-day certificate publication is workstation-only")
    with closing(client_factory(_workstation_clickhouse_url())) as http:
        client = MarketDayCertificateClient(http)
        with closing(keeper_session_factory()) as session:
            reader = MarketDayKeeperReader(session.client)
            existing = reader.load(build_id)
            if existing is not None:
                audit_attested_market_day_certificate(client, reader, build_id,
                                                       sessions=sessions)
                _exact(client, prepared, build_id, tuple(table.name for table in TABLES))
                return {"build_id": build_id, "sessions": sessions,
                        "family_rows": counts, "status": "already_attested"}
            authority = MarketDayKeeperAuthority(session.client)
            claim = authority.acquire(build_id, f"market-day-cert-{uuid4().hex}")
            if claim is None:
                raise RuntimeError("Market-day certificate build is owned by another publisher")
            proof = publish_market_day_certificate(
                client, CanonicalSourceReader(http), authority, claim, prepared,
                sessions=sessions)
            audit_attested_market_day_certificate(client, reader, build_id,
                                                   sessions=sessions)
            return {"build_id": build_id, "sessions": sessions,
                    "family_rows": counts, "status": "attested",
                    "definition_hash": proof.definition_hash}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-market-certificate-publication", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_market_certificate_publication:
        parser.error("--apply requires --confirm-market-certificate-publication")
    try:
        result = publish_saved_build(args.runtime, args.build_id, apply=args.apply)
        print(f"Market-day certificate {result['status']}: {result['build_id']}; "
              f"{sum(result['family_rows'].values())} typed rows")
    except KeyboardInterrupt:
        print("Interrupted; inspect typed rows and rerun to reconcile.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Market-day certificate blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Producer-only publication of per-session seals for one attested V5 build.

Dry-run by default. --apply writes only arte.market_day_session_seal_v1 and
matching Keeper receipts; it never changes bars, indicators, liquidity, or
global certificate tables. Rerun after interruption to reconcile exact rows.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import platform
import re
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.install_market_day_certificate_layout import (
    workstation_clickhouse_url,
)
from scripts.clickhouse.publish_market_day_certificate import _certificate_admin_client
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import (
    MarketDayKeeperAuthority, MarketDayKeeperReader,
)
from src.trading_runtime.arte_market_day_session_seal import (
    SESSION_SEAL, MarketDaySessionSealClient, inspect_session_seal,
    prepare_session_seal, publish_session_seal,
)
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def publish_build_sessions(
    build_id: str, *, selected_days: tuple[date, ...] = (), apply: bool,
    client_factory=_certificate_admin_client,
    keeper_session_factory=open_workstation_keeper_session,
) -> dict[str, int]:
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", build_id)):
        raise ValueError("Session-seal producer requires a workstation V5 build ID")
    url = workstation_clickhouse_url()
    with closing(client_factory(url)) as http:
        client = MarketDaySessionSealClient(http)
        storage_preflight(client, tables=(SESSION_SEAL,))
        source_days = [json.loads(line)["session_date"] for line in client.execute(
            "SELECT session_date FROM arte.market_day_source_session_v1 "
            f"WHERE build_id='{build_id}' AND requested=1 "
            "ORDER BY session_date FORMAT JSONEachRow"
        ).splitlines() if line.strip()]
        if (not source_days or len(source_days) != len(set(source_days))
                or any(date.fromisoformat(day).isoformat() != day
                       for day in source_days)):
            raise RuntimeError("Attested build has no unique requested sessions")
        wanted = tuple(sorted(day.isoformat() for day in selected_days)) or tuple(source_days)
        if len(wanted) != len(set(wanted)) or not set(wanted) <= set(source_days):
            raise ValueError("Selected sessions are outside the attested build")
        with closing(keeper_session_factory()) as session:
            reader = MarketDayKeeperReader(session.client)
            audit = audit_attested_market_day_certificate(
                client, reader, build_id, sessions=(wanted[0],),
                read_client_factory=lambda: MarketDaySessionSealClient(client_factory(url)),
                include_session_hashes=True,
            )
            if audit.attestation != reader.load(build_id):
                raise RuntimeError("Market-day Keeper proof changed during seal preparation")
            rows = [prepare_session_seal(
                audit.certificate, audit.attestation,
                session_date=date.fromisoformat(day),
            ) for day in wanted]
            before = [inspect_session_seal(client, reader, audit.attestation, row)
                      for row in rows]
            counts = {
                "sessions": len(rows),
                "committed_before": before.count("committed"),
                "rows_unsealed_before": before.count("row_unsealed"),
                "absent_before": before.count("absent"),
                "committed_now": 0,
            }
            if not apply:
                return counts
            authority = MarketDayKeeperAuthority(session.client)
            if authority.load(build_id) != audit.attestation:
                raise RuntimeError("Market-day Keeper proof changed before publication")
            for index, row in enumerate(rows, 1):
                publish_session_seal(client, authority, audit.attestation, row)
                counts["committed_now"] += 1
                print(f"Sealed {index}/{len(rows)}: {row['session_date']}", flush=True)
            return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--session", type=date.fromisoformat, action="append",
                        help="repeat for selected dates; omit to seal all requested sessions")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-market-day-session-seals", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_market_day_session_seals:
        parser.error("--apply requires --confirm-market-day-session-seals")
    started = perf_counter()
    try:
        counts = publish_build_sessions(
            args.build_id, selected_days=tuple(args.session or ()), apply=args.apply)
    except KeyboardInterrupt:
        print("Interrupted; committed seals remain. Rerun to reconcile.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Session-seal publication blocked: {type(exc).__name__}; "
              "verify layout, root proof, and selected-day rows.", file=sys.stderr)
        return 1
    print(f"Session seals {'applied' if args.apply else 'planned'}: "
          f"sessions={counts['sessions']} committed_before={counts['committed_before']} "
          f"row_unsealed={counts['rows_unsealed_before']} "
          f"absent={counts['absent_before']} committed_now={counts['committed_now']} "
          f"elapsed_s={perf_counter() - started:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

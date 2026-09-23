#!/usr/bin/env python3
"""Audit pre-open tradable populations; certify retained evidence with --execute.

The script never reconstructs a past universe from the current identity graph.
Unresolved sessions remain unavailable to historical consumers.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
import uuid

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.env import discover_env_files, load_env_files
from services.reference_gateway.tradable_snapshots import (
    COVERAGE, REVISION, ensure_schema, publish_retained_snapshot, query, record_missing_sessions,
    session_cutoff, sql_literal, target_session,
)

RUNTIME = Path("D:/TradingML/runtimes")


def retained_publication_completion(run_id: str) -> datetime:
    prefix = "step_06_bridge_features_"
    suffix = run_id.removeprefix(prefix)
    if not run_id.startswith(prefix) or not re.fullmatch(r"\d{8}_\d{6}", suffix):
        raise ValueError("No verifiable Step 06 source run")
    roots = (RUNTIME / "reference_gateway" / "tradable_rebuilds",
        Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\reference_gateway\tradable_rebuilds"))
    for root in roots:
        execution = root / suffix / "step_06_execution.jsonl"
        validation = root / suffix / "step_06_validation.jsonl"
        if not execution.is_file() or not validation.is_file():
            continue
        rows = [json.loads(line) for line in execution.read_text(encoding="utf-8").splitlines()]
        checks = [json.loads(line) for line in validation.read_text(encoding="utf-8").splitlines()]
        finished = [row for row in rows if row.get("name")=="tradable_universe" and row.get("status")=="ok"]
        valid = [row for row in checks if row.get("name")=="tradable_universe" and row.get("status")=="pass"]
        if len(finished)!=1 or len(valid)!=1:
            raise ValueError(f"{run_id}: retained publication did not finish and validate")
        return datetime.fromisoformat(finished[0]["finished_at_utc"]).astimezone(UTC)
    raise ValueError(f"{run_id}: retained publication completion evidence unavailable")


def audit(client: ClickHouseHttpClient, start: date, end: date, *, execute: bool) -> dict:
    import pandas_market_calendars as mcal

    sessions = [stamp.date() for stamp in mcal.get_calendar("XNYS").schedule(start_date=start,end_date=end).index]
    # Include prior-weekend publications and snapshots taken before the first open.
    first_source = start - timedelta(days=5)
    sources = query(client, f"""SELECT universe_date,uniqExact(source_run_id) AS runs,
        min(source_run_id) AS run_id,min(inserted_at) AS first_insert,max(inserted_at) AS last_insert,count() AS rows
        FROM q_live.feature_tradable_universe_v1 FINAL
        WHERE universe_date BETWEEN toDate({sql_literal(first_source)}) AND toDate({sql_literal(end)})
        GROUP BY universe_date ORDER BY universe_date""")
    candidates: dict[date,list[dict]] = {day:[] for day in sessions}
    rejected = []
    for source in sources:
        if int(source["runs"]) != 1 or source["first_insert"] != source["last_insert"]:
            rejected.append(dict(source_date=source["universe_date"],reason="mixed_source_or_capture"))
            continue
        captured = datetime.fromisoformat(source["last_insert"]).replace(tzinfo=UTC)
        session = target_session(captured)
        if session in candidates:
            try:
                available = retained_publication_completion(source["run_id"])
            except ValueError as exc:
                rejected.append(dict(source_date=source["universe_date"],reason=str(exc)))
                continue
            if available >= session_cutoff(session):
                rejected.append(dict(source_date=source["universe_date"],reason="publication_completed_after_preopen_cutoff"))
                continue
            candidates[session].append({**source,"available_at_utc":available.isoformat()})
    certificates = {}
    if execute:
        ensure_schema(client)
        for day in sessions:
            if not candidates[day]:
                continue
            source = max(candidates[day],key=lambda row:row["last_insert"])
            try:
                publish_retained_snapshot(client,"q_live",date.fromisoformat(source["universe_date"]),
                    available_at_utc=datetime.fromisoformat(source["available_at_utc"]),expected_session=day)
            except ValueError as exc:
                # A newer certified snapshot wins; other errors remain explicit.
                if "newer or equal certified snapshot" not in str(exc):
                    rejected.append(dict(session_date=str(day),source_date=source["universe_date"],reason=str(exc)))
        record_missing_sessions(client,"q_live",start,end)
    coverage_exists = query(client,f"SELECT count() AS n FROM system.tables WHERE database='q_live' AND name={sql_literal(COVERAGE)}")[0]['n'] == 1
    if coverage_exists:
        availability_exists = query(client,f"SELECT count() AS n FROM system.columns WHERE database='q_live' AND table={sql_literal(COVERAGE)} AND name='available_at_utc'")[0]['n'] == 1
        available_column = "available_at_utc," if availability_exists else ""
        rows = query(client, f"SELECT session_date,snapshot_id,source_universe_date,captured_at_utc,{available_column}row_count,tradable_count,source_hash,revision,status FROM q_live.{COVERAGE} FINAL WHERE session_date BETWEEN toDate({sql_literal(start)}) AND toDate({sql_literal(end)})")
        certificates = {date.fromisoformat(row["session_date"]):row for row in rows if row["status"]=="certified" and row['revision']==REVISION}
    outcomes = []
    for day in sessions:
        source = max(candidates[day],key=lambda row:row["last_insert"]) if candidates[day] else None
        certificate = certificates.get(day)
        status = "certified" if certificate else ("recoverable" if source else "unresolved_no_preopen_capture")
        outcomes.append(dict(session_date=str(day),status=status,
            source_universe_date=certificate["source_universe_date"] if certificate else source["universe_date"] if source else None,
            captured_at_utc=certificate["captured_at_utc"] if certificate else source["last_insert"] if source else None,
            available_at_utc=certificate["available_at_utc"] if certificate else source["available_at_utc"] if source else None,
            snapshot_id=certificate["snapshot_id"] if certificate else None))
    return dict(start_date=str(start),end_date=str(end),execute=execute,revision=REVISION,
                sessions=outcomes,rejected_sources=rejected,
                counts={status:sum(row["status"]==status for row in outcomes) for status in ("certified","recoverable","unresolved_no_preopen_capture")})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date",required=True)
    parser.add_argument("--end-date",required=True)
    parser.add_argument("--execute",action="store_true",help="Copy and certify retained pre-open snapshots on live_market_ssd")
    args = parser.parse_args()
    start,end = date.fromisoformat(args.start_date),date.fromisoformat(args.end_date)
    if end < start:
        parser.error("--end-date precedes --start-date")
    if (end-start).days > 366:
        parser.error("Audit one year or less per run")
    if not RUNTIME.is_dir():
        raise RuntimeError("Required D:/TradingML/runtimes is unavailable")
    load_env_files(discover_env_files(ROOT))
    client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password())
    report=audit(client,start,end,execute=args.execute)
    output=RUNTIME / "reference_gateway" / "universe_audits"
    output.mkdir(parents=True,exist_ok=True)
    path=output / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]+".json")
    path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(f"Tradable snapshot audit {start}..{end} | {len(report['sessions'])} sessions | "
          f"certified {report['counts']['certified']} recoverable {report['counts']['recoverable']} "
          f"unresolved {report['counts']['unresolved_no_preopen_capture']}")
    for row in report["sessions"]:
        print(f"  {row['session_date']}  {row['status']}  source={row['source_universe_date'] or '-'}")
    print(f"Manifest: {path}")
    return 0 if not report["counts"]["unresolved_no_preopen_capture"] and not report["rejected_sources"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

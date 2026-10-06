"""Read-only date selection from published scope and completed producer manifests.

Catalogue availability is not full strategy-input certification. The real tape
preflight verifies every selected product, source attempt and Keeper proof.
"""
from contextlib import closing
from datetime import date, timedelta
import json
import os
from pathlib import Path

from .runtime import ROOT
from .source.arte_source import load_build

WINDOWS = {"premarket": ("04:00", "09:30"), "regular": ("09:30", "16:00"),
           "afterhours": ("16:00", "20:00")}


def configure_reader(repo):
    from research.mlops.env import discover_env_files, load_env_files
    load_env_files(discover_env_files(repo), verbose=False)
    # File location is local to the executing host; never read/copy secret values.
    prefix = "BACKTEST_V3_READ_CLICKHOUSE_"
    credential = ROOT.parent / "secrets" / "backtest_v3_read.env"
    if (not os.environ.get("BACKTEST_V3_READ_CREDENTIAL_FILE")
            and not any(os.environ.get(prefix + k) for k in ("URL", "USER", "PASSWORD"))
            and credential.is_file()):
        os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(credential)


def discover_sources(runtime_root=ROOT, reader=None):
    """Intersect published V5 build/date scope with matching producer certificates."""
    if reader is None:
        from src.backend.backtest_v3_clients import v3_client
        with closing(v3_client("read")) as client:
            return discover_sources(runtime_root, client)
    rows = [json.loads(line) for line in reader.execute(
        "SELECT s.build_id,toString(s.session_date) AS day,count() AS tickers "
        "FROM arte.market_day_planned_scope_v1 s INNER JOIN arte.market_day_build_fence_v1 f "
        "ON s.build_id=f.build_id GROUP BY s.build_id,s.session_date ORDER BY day FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    published = {(r["build_id"], r["day"]): int(r["tickers"]) for r in rows}
    if len(published) != len(rows):
        raise ValueError("Duplicate published market-day date identity")
    ledger = Path(runtime_root) / "build-ledger-v2.sqlite3"
    sources, rejected = {}, []
    # Bounded producer directories only: no recursive runtime/history scan.
    for directory in sorted(Path(runtime_root).glob("market-day*")):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if len(path.stem) != 64 or any(c not in "0123456789abcdef" for c in path.stem):
                continue
            report = json.loads(path.read_text(encoding="utf-8-sig"))
            definition = report.get("definition", {})
            if report.get("status") != "core_complete" or definition.get("version") != "market-day-core-v5":
                continue
            days = [d for d in definition["plan"]["requested"] if (report["build_id"], d) in published]
            if not days:
                continue
            proof = load_build(path, ledger, days)  # SQLite read-only, exact full population.
            for day in days:
                if len(proof["units"][day]) != published[report["build_id"], day]:
                    raise ValueError(f"{day}: manifest/published population count differs")
                value = {"day": day, "build_id": report["build_id"], "manifest": str(path.resolve()),
                         "ledger": str(ledger.resolve()), "tickers": published[report["build_id"], day]}
                if day in sources and sources[day]["build_id"] != value["build_id"]:
                    raise ValueError(f"{day}: overlapping completed builds; select explicit --manifest")
                sources[day] = value
    for build, day in published:
        if day not in sources:
            rejected.append({"day": day, "build_id": build, "reason": "no matching completed V5 manifest"})
    if not sources:
        raise ValueError("No published V5 dates with matching completed local producer certificates")
    return {"sources": [sources[d] for d in sorted(sources)], "unavailable": rejected,
            "qualification": "catalogue only; full liquidity/MACD/V7/identity preflight still required"}


def select_dates(sources, *, single=None, start=None, end=None, preflight=False):
    available = {s["day"]: s for s in sources}
    if single and (start or end):
        raise ValueError("Use --date OR --from/--to")
    if single:
        date.fromisoformat(single)
        if single not in available:
            raise ValueError(f"Requested date {single} is not in the certified source catalogue")
        selected = [available[single]]
    else:
        lo, hi = date.fromisoformat(start or min(available)), date.fromisoformat(end or max(available))
        if lo > hi or lo.isoformat() < min(available) or hi.isoformat() > max(available):
            raise ValueError("Range must be ordered and within available catalogue boundaries")
        selected = [available[d] for d in sorted(available) if lo.isoformat() <= d <= hi.isoformat()]
    if not selected:
        raise ValueError("No available trading dates in requested range")
    import pandas_market_calendars as mcal
    schedule = mcal.get_calendar("XNYS").schedule(start_date=lo if not single else single,
                                                end_date=hi if not single else single)
    expected = {d.date().isoformat() for d in schedule.index}
    if expected - {s["day"] for s in selected}:
        raise ValueError("Missing expected trading-date coverage: " + ", ".join(sorted(expected - {s["day"] for s in selected})))
    if preflight:
        selected = selected[-1:]  # One latest selected session, not an entire campaign.
    chosen = {s["day"] for s in selected}
    lo, hi = date.fromisoformat(selected[0]["day"]), date.fromisoformat(selected[-1]["day"])
    absent = [(lo + timedelta(days=i)).isoformat() for i in range((hi-lo).days+1)
              if (lo + timedelta(days=i)).isoformat() not in chosen]
    return selected, absent


def session_bounds(day, name):
    import pandas_market_calendars as mcal
    schedule = mcal.get_calendar("XNYS").schedule(start_date=day, end_date=day)
    if len(schedule) != 1:
        raise ValueError(f"{day}: no exchange session")
    opened = schedule.iloc[0]["market_open"].tz_convert("America/New_York").strftime("%H:%M")
    closed = schedule.iloc[0]["market_close"].tz_convert("America/New_York").strftime("%H:%M")
    return {"premarket": ("04:00", opened), "regular": (opened, closed), "afterhours": (closed, "20:00")}[name]

"""Bounded hindsight price diagnostics from completed saved runs and pinned bars.

These extrema are descriptive, not executable signals or counterfactual P&L.
No database writes, flatfiles, current reference data, or synthetic prices.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, time
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from uuid import UUID
from zoneinfo import ZoneInfo

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.clickhouse.report_strategy_one_trades import SelectOnly, RUNTIME_ROOT
from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_chart import certified_saved_run_plan
from src.backend.backtest_v4_saved_review import load_v4_performance_report, load_v4_terminal_review_page
from src.trading_runtime.arte_backtest_definition import load_backtest_definition
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env

DEFAULT_RUNS = ("b62fa860-7570-4976-b23f-58b416b56a42", "1cd937c3-2dd1-4369-a477-bc74e532c94d",
                "b06469c5-fded-4f7a-a944-d0837d17af81")
NY = ZoneInfo("America/New_York")


def local_us(stamp: str, session) -> int:
    value = datetime.fromisoformat(stamp)
    if value.tzinfo is None or value.astimezone(NY).date() != session:
        raise ValueError("Episode timestamp is not an aware instant on its certified session")
    delta = value.astimezone(NY) - datetime.combine(session, time(), NY)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def bucket_window(entry_us: int, exit_us: int, end_us: int, resolution_ms: int,
                  horizon: str) -> tuple[int, int]:
    """Return [lower, upper) midnight bucket indexes, never a mixed fill bucket.

    A fill timestamp is the completed broker bucket boundary. For coarser bars,
    exclude any bar straddling the fill. In-position also excludes the exit
    filled bucket; post-exit starts with the first whole subsequent bar.
    """
    step = resolution_ms * 1000
    ceil = lambda value: (value + step - 1) // step
    if not 0 <= entry_us <= exit_us <= end_us or resolution_ms not in (100, 1000):
        raise ValueError("Invalid excursion boundary contract")
    if horizon == "held":
        lower, upper = ceil(entry_us), ceil(exit_us) - 1
    elif horizon in {"post_5m", "post_15m"}:
        minutes = 5 if horizon == "post_5m" else 15
        lower = ceil(exit_us)
        upper = min(exit_us + minutes * 60_000_000, end_us) // step
    else:
        raise ValueError("Unknown excursion horizon")
    return lower, max(lower, upper)


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def aggregate_sql(plan, session, requests: list[dict]) -> str:
    """One vectorized SELECT for at most 100 episodes x six diagnostic windows."""
    if not requests or len(requests) > 600:
        raise ValueError("Excursion query exceeds its bounded request shard")
    tuples = ",".join("(" + ",".join((literal(row["episode_id"]), literal(row["ticker"]),
        literal(row["attempt_id"]), str(row["resolution_ms"]), literal(row["horizon"]),
        str(row["lower"]), str(row["upper"]))) + ")" for row in requests)
    scopes = sorted({(row["ticker"], row["attempt_id"]) for row in requests})
    pins = ",".join(f"({literal(ticker)},toUUID({literal(attempt)}))" for ticker, attempt in scopes)
    bounds = []
    for resolution in (100, 1000):
        subset = [row for row in requests if row["resolution_ms"] == resolution]
        if subset:
            bounds.append(f"(resolution_ms={resolution} AND bucket_index>={min(row['lower'] for row in subset)} "
                          f"AND bucket_index<{max(row['upper'] for row in subset)})")
    return f"""SELECT r.episode_id,r.resolution_ms,r.horizon,
      count() AS observed_bar_buckets,uniqExact(b.bucket_index) AS unique_bar_buckets,
      countIf(b.valid) AS valid_extrema_buckets,
      if(countIf(b.valid)>0,minIf(b.low_int,b.valid),NULL) AS low_int,
      if(countIf(b.valid)>0,maxIf(b.high_int,b.valid),NULL) AS high_int
    FROM (SELECT x.1 AS episode_id,x.2 AS ticker,toUUID(x.3) AS attempt_id,
                 x.4 AS resolution_ms,x.5 AS horizon,x.6 AS lower,x.7 AS upper
          FROM (SELECT arrayJoin([{tuples}]) AS x)) AS r
    INNER JOIN (SELECT ticker,attempt_id,resolution_ms,bucket_index,low_int,high_int,
                       price_valid=1 AND extremes_valid=1 AND low_int>0 AND high_int>=low_int AS valid
                FROM arte.bars_v1 WHERE build_id={literal(plan.build_id)}
                  AND session_date=toDate({literal(session.isoformat())})
                  AND (ticker,attempt_id) IN ({pins}) AND ({' OR '.join(bounds)})) AS b
      ON r.ticker=b.ticker AND r.attempt_id=b.attempt_id AND r.resolution_ms=b.resolution_ms
    WHERE b.bucket_index>=r.lower AND b.bucket_index<r.upper
    GROUP BY r.episode_id,r.resolution_ms,r.horizon
    ORDER BY r.episode_id,r.resolution_ms,r.horizon FORMAT JSONEachRow"""


def build(journal, market, run_id: str) -> dict:
    terminal = load_v4_terminal_review_page(journal, run_id, after_sequence=0, limit=1)
    if terminal["status"] != "completed":
        raise ValueError("Excursions require a completed run; failed runs are excluded")
    session, context, _, plan = certified_saved_run_plan(journal, market, run_id=run_id)
    definition = load_backtest_definition(journal, run_id, run_context=context)["definition"]
    start_ms, end_ms = int(definition["start_local_ms"]), int(definition["end_local_ms"])
    if not ((14_400_000 <= start_ms < end_ms <= 34_200_000)
            or (57_600_000 <= start_ms < end_ms <= 72_000_000)):
        raise ValueError("Select one saved premarket or after-hours window; no RTH extension")
    page = load_v4_performance_report(journal, run_id)
    if any(row["status"] != "closed" for row in page["position_lifecycles"]):
        raise ValueError("Excursions require zero open terminal lifecycles")
    episodes = sorted(page["report"]["episodes"], key=lambda row: (row["opened_at"], row["episode_id"]))
    if len(episodes) > 2000 or len({row["episode_id"] for row in episodes}) != len(episodes):
        raise ValueError("Excursions require at most 2000 unique episodes per run")
    positions, requests, query_hashes = [], [], []
    for episode in episodes:
        ticker = episode["instrument"]["symbol"]
        units = [unit for unit in plan.units if unit.stage == "bars" and unit.ticker == ticker
                 and unit.session_date == session.isoformat()]
        if len(units) != 1 or not {100, 1000} <= set(plan.required_resolutions_ms):
            raise ValueError("Episode has no unique certified 100ms/1s bar source")
        entry_us, exit_us = (local_us(episode[key], session) for key in ("opened_at", "closed_at"))
        if not start_ms * 1000 <= entry_us <= exit_us <= end_ms * 1000 or episode["side"] != "LONG":
            raise ValueError("Episode is outside the requested long-only saved window")
        position = {key: episode[key] for key in ("episode_id", "opened_at", "closed_at", "entry_price", "exit_price", "quantity", "fees", "net_pnl")}
        position.update(ticker=ticker, bars_attempt_id=units[0].attempt_id, diagnostics=[])
        positions.append(position)
        for resolution in (100, 1000):
            for horizon in ("held", "post_5m", "post_15m"):
                lower, upper = bucket_window(entry_us, exit_us, end_ms * 1000, resolution, horizon)
                requests.append(dict(episode_id=episode["episode_id"], ticker=ticker, attempt_id=units[0].attempt_id,
                                     resolution_ms=resolution, horizon=horizon, lower=lower, upper=upper))
    results = {}
    nonempty = [row for row in requests if row["lower"] < row["upper"]]
    for offset in range(0, len(nonempty), 600):
        query = aggregate_sql(plan, session, nonempty[offset:offset + 600])
        query_hashes.append(sha256(query.encode()).hexdigest())
        for line in market.execute(query).splitlines():
            if line.strip():
                row = json.loads(line)
                key = row["episode_id"], int(row["resolution_ms"]), row["horizon"]
                if key in results or int(row["observed_bar_buckets"]) != int(row["unique_bar_buckets"]):
                    raise ValueError("Pinned bars have duplicate diagnostic keys")
                results[key] = row
    by_id = {row["episode_id"]: row for row in positions}
    for request in requests:
        position = by_id[request["episode_id"]]
        row = results.get((request["episode_id"], request["resolution_ms"], request["horizon"]), {})
        observed, valid = int(row.get("observed_bar_buckets", 0)), int(row.get("valid_extrema_buckets", 0))
        grid = request["upper"] - request["lower"]
        if not 0 <= valid <= observed <= grid:
            raise ValueError("Bar diagnostic counts exceed their exact boundary grid")
        prices = {name: None if row.get(f"{name}_int") is None else str(Decimal(str(row[f"{name}_int"])) / 10000)
                  for name in ("low", "high")}
        entry, exit_price = Decimal(str(position["entry_price"])), Decimal(str(position["exit_price"]))
        denominator = entry if request["horizon"] == "held" else exit_price
        changes = {f"{name}_pct": None if value is None else str((Decimal(value) / denominator - 1) * 100)
                   for name, value in prices.items()}
        position["diagnostics"].append({"resolution_ms": request["resolution_ms"], "horizon": request["horizon"],
            "bucket_lower_inclusive": request["lower"], "bucket_upper_exclusive": request["upper"],
            "grid_buckets": grid, "observed_bar_buckets": observed, "absent_bar_buckets": grid - observed,
            "invalid_extrema_buckets": observed - valid, "valid_extrema_buckets": valid,
            "status": "available" if valid else "empty_window" if not grid else "no_valid_bars",
            **prices, **changes})
    return {"schema_version": "numbered-trade-hindsight-extrema-v1", "run_id": run_id,
        "session_date": session.isoformat(), "strategy_number": context["strategy_revision"],
        "verified_sequence": page["verified_sequence"], "market_plan_token": plan.token,
        "market_build_id": plan.build_id, "saved_start_local_ms": start_ms, "saved_end_local_ms": end_ms,
        "query_hashes": query_hashes, "positions": positions,
        "limitations": ["Hindsight diagnostics only; not executable signals or counterfactual portfolio P&L.",
            "Held windows exclude entry filled bucket and ambiguous exit bucket; post-exit uses fully subsequent completed bars.",
            "Post-exit horizons stop at the saved requested session end; no regular-session spillover.",
            "Held percentages use final weighted entry price, which can include later adds; not causal MFE/MAE or drawdown.",
            "Post-exit percentages use weighted exit price; extrema are not fills and exclude fees/spread/available size.",
            "Absent sparse bar buckets may indicate no eligible trade, not missing certified coverage; retained separately from invalid bars."]}


def markdown(report):
    lines = [f"# Hindsight extrema — {report['session_date']}", "", f"Run `{report['run_id']}`", "",
             *[f"- {item}" for item in report["limitations"]], "",
             "100 ms extrema below; JSON also contains independent 1s results and coverage counts. Percentages are descriptive.", "",
             "| Ticker / episode | Net P&L | Held low % | Held high % | Post 5m low / high % | Post 15m low / high % |",
             "|---|---:|---:|---:|---:|---:|"]
    def fmt(value):
        return "n/a" if value is None else f"{Decimal(str(value)):.2f}"
    for position in sorted(report["positions"], key=lambda row: Decimal(str(row["net_pnl"]))):
        rows = {row["horizon"]: row for row in position["diagnostics"] if row["resolution_ms"] == 100}
        values = [f"{position['ticker']} / {position['episode_id']}", fmt(position["net_pnl"]),
                  fmt(rows["held"]["low_pct"]), fmt(rows["held"]["high_pct"])]
        values.extend(f"{fmt(rows[key]['low_pct'])} / {fmt(rows[key]['high_pct'])}" for key in ("post_5m", "post_15m"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def persist(directory, report):
    outputs = {"excursions.json": (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
               "excursions.md": markdown(report).encode()}
    for name, content in outputs.items():
        path = directory / name
        if path.exists() and path.read_bytes() != content:
            raise RuntimeError(f"Immutable output differs: {path}; choose a new output directory")
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        path = directory / name
        if path.exists():
            continue
        with NamedTemporaryFile(dir=directory, suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return sha256(outputs["excursions.json"]).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", action="append", help="completed run UUID; repeat up to 20 times")
    parser.add_argument("--output", type=Path, default=RUNTIME_ROOT / "strategy_one_research" / "baseline_excursions_v1")
    args = parser.parse_args()
    completed = 0
    try:
        runs = [str(UUID(value)) for value in (args.run_id or DEFAULT_RUNS)]
        if len(runs) > 20 or len(runs) != len(set(runs)):
            raise ValueError("Use at most 20 unique completed runs")
        output = args.output.resolve()
        if not RUNTIME_ROOT.is_dir() or not output.is_relative_to(RUNTIME_ROOT.resolve()):
            raise ValueError("Output must stay within the available managed runtime root")
        _load_private_credentials()
        with closing(backtest_v4_operator_client_from_env()) as journal, closing(v3_client("read")) as market:
            for index, run_id in enumerate(runs):
                print(f"Runs active=1 queued={len(runs)-index-1} completed={completed} skipped=0 retried=0 failed=0\nReading {run_id}", flush=True)
                report = build(SelectOnly(journal), SelectOnly(market), run_id)
                digest = persist(output / run_id, report)
                completed += 1
                missing = sum(row["status"] != "available" for position in report["positions"] for row in position["diagnostics"])
                print(f"Saved {len(report['positions'])} episodes; {missing} empty/unavailable windows; SHA256 {digest}\n{output / run_id}", flush=True)
        print(f"Complete: active=0 queued=0 completed={completed} skipped=0 retried=0 failed=0")
        return 0
    except KeyboardInterrupt:
        print(f"Interrupted: {completed} immutable reports complete; rerun to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Failed: completed={completed} failed=1; {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
        MARKET_CERTIFICATE_KEEPER_POOL.close()

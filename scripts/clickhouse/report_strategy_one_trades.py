"""Read certified numbered fixed Backtests and persist immutable research reports.

No database mutations. Repeating a run verifies identical output; a different
projection requires a new output directory rather than replacing evidence.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
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

from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.backend.backtest_market_data import assert_select_only
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_chart import certified_saved_run_plan
from src.backend.backtest_v4_saved_review import load_v4_performance_report, load_v4_terminal_review_page
from src.backend.strategy_one_entry_context import pinned_entry_volume
from src.backend.backtest_v4_performance_evidence import load_broker_observed_drawdown
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env
from src.trading_runtime.domain import json_safe

RUNTIME_ROOT = Path("D:/TradingML/runtimes")
DEFAULT_RUNS = ("b62fa860-7570-4976-b23f-58b416b56a42",
                "1cd937c3-2dd1-4369-a477-bc74e532c94d")
NY = ZoneInfo("America/New_York")


class SelectOnly:
    """Enforce read-only SQL even when journal credentials permit writes."""

    def __init__(self, client):
        self.client = client
        self.base_url = client.base_url
        self.user = client.user
        self.password = client.password

    def execute(self, query, *args, **kwargs):
        return self.client.execute(assert_select_only(query), *args, **kwargs)


def et(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("Report timestamp lacks timezone")
    return stamp.astimezone(NY).isoformat(timespec="microseconds")


def build_report(journal, market, run_id: str) -> dict:
    terminal = load_v4_terminal_review_page(journal, run_id, after_sequence=0, limit=1)
    if terminal["status"] != "completed":
        raise RuntimeError("Optimization report requires a completed run")
    session, context, _, plan = certified_saved_run_plan(journal, market, run_id=run_id)
    number = int(context["strategy_revision"])
    numbered_evidence = {}
    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36):
        from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
        release = certify_numbered_configuration(market, number)
        if release.payload_hash != context["configuration_hash"]:
            raise RuntimeError("Numbered report differs from the sealed run configuration")
        numbered_evidence = {"strategy_number": number,
                             "numbered_release": release.payload["strategy"]["numbered_release"],
                             "configuration_release_token": release.token}
    page = load_v4_performance_report(journal, run_id)
    marked_drawdown = load_broker_observed_drawdown(journal, run_id)
    if marked_drawdown["verified_terminal_sequence"] != page["verified_sequence"]:
        raise RuntimeError("Report and broker drawdown committed heads differ")
    open_count = sum(row["status"] != "closed" for row in page["position_lifecycles"])
    if open_count:
        raise RuntimeError(f"Optimization report requires flat terminal positions; open lifecycles={open_count}")
    if Decimal(str(context["initial_cash"])) <= 0:
        raise RuntimeError("Certified initial cash must be positive")
    lifecycles = {row["episode_id"]: row for row in page["position_lifecycles"]}
    rows = []
    seen = set()
    for episode in sorted(page["report"]["episodes"],
                          key=lambda row: (row["opened_at"], row["episode_id"])):
        identity = episode["episode_id"]
        if identity in seen:
            raise RuntimeError("Duplicate episode identity")
        seen.add(identity)
        lifecycle = lifecycles[identity]
        ticker = episode["instrument"]["symbol"]
        entry = datetime.fromisoformat(episode["opened_at"])
        volumes = pinned_entry_volume(market, plan, session=session,
                                      ticker=ticker, entry_at=entry)
        units = [unit for unit in plan.units if unit.stage == "bars"
                 and unit.ticker == ticker and unit.session_date == session.isoformat()]
        reason = lifecycle.get("exit_reason") or lifecycle.get("presentation_exit_reason")
        rows.append({**episode, "run_id": run_id, "ticker": ticker,
                     "peak_quantity": lifecycle["peak_quantity"],
                     "entry_et": et(episode["opened_at"]),
                     "exit_et": et(episode["closed_at"]),
                     "causal_exit_reason": reason or "unavailable",
                     "exit_reason_source": "journal" if lifecycle.get("exit_reason")
                     else lifecycle.get("presentation_exit_reason_source", "effective_protection_order") if reason else "unavailable",
                     "exit_components": lifecycle.get("exit_components", []),
                     "protection_timeline": lifecycle.get("protection_timeline", []),
                     "bars_attempt_id": units[0].attempt_id,
                     "entry_completed_volume": volumes,
                     "float_shares": None,
                     "float_status": "unavailable: no dedicated as-of reference reader configured"})
    return json_safe({"schema_version": "numbered-fixed-research-trades-v4",
                      "report_source_hashes": {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                          for path in (Path(__file__), ROOT / "src/backend/backtest_v4_saved_review.py",
                                       ROOT / "src/backend/backtest_v4_performance_evidence.py")},
                      "broker_observed_drawdown": marked_drawdown, **numbered_evidence,
                      "run_id": run_id, "session_date": session.isoformat(),
                      "status": terminal["status"], "open_lifecycle_count": open_count,
                      "initial_cash": context["initial_cash"],
                      "verified_sequence": page["verified_sequence"],
                      "market_build_id": plan.build_id, "market_plan_token": plan.token,
                      "saved_context": context,
                      "drawdown_scope": "closed episodes only; not intratrade or mark-to-market",
                      "volume_scope": "completed pinned 1s bars through entry; forming second excluded",
                      "report": page["report"], "positions": rows})


def markdown(report: dict) -> str:
    number = report.get("strategy_number", 1)
    lines = [f"# Strategy {number} positions: {report['session_date']}", "",
             f"Run: `{report['run_id']}`; verified sequence: {report['verified_sequence']}.", "",
             f"Status: {report['status']}; open lifecycles: {report['open_lifecycle_count']}; initial cash: {report['initial_cash']}.", "",
             "Sorted by net P&L ascending, then episode ID. Times are America/New_York with UTC offset.",
             "Quantity is peak position quantity; prices are weighted execution averages.",
             "The original report drawdown covers closed episodes only.",
             f"Broker-observed marked-equity maximum drawdown: {report['broker_observed_drawdown']['maximum_drawdown']:,.2f}. "
             "Marks update asynchronously and can be stale without an age limit; this is not synchronized equity or liquidation value.",
             "Float is unavailable: no dedicated as-of reference reader is configured.", "",
             "| Episode | Ticker | Entry ET | Exit ET | Entry price | Exit price | Peak qty | Fees | Net P&L | Causal exit | Session volume at entry | Last 60s volume |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|"]
    for row in sorted(report["positions"], key=lambda row: (Decimal(str(row["net_pnl"])), row["episode_id"])):
        volume = row["entry_completed_volume"]
        def number(value, places):
            return f"{Decimal(str(value)):,.{places}f}"
        values = [row["episode_id"], row["ticker"], row["entry_et"], row["exit_et"],
                  number(row["entry_price"], 4), number(row["exit_price"], 4),
                  number(row["peak_quantity"], 0), number(row["fees"], 2),
                  number(row["net_pnl"], 2), row["causal_exit_reason"],
                  number(volume["session_volume"], 0), number(volume["last_minute_volume"], 0)]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    return "\n".join(lines) + "\n"


def persist(directory: Path, report: dict) -> str:
    payload = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    outputs = {directory / "report.json": payload,
               directory / "positions.md": markdown(report).encode()}
    # Check all existing files before publishing any missing companion file.
    for path, content in outputs.items():
        if path.exists() and path.read_bytes() != content:
            raise RuntimeError(f"Immutable output differs: {path}; choose a new --output directory")
    directory.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        if not path.exists():
            # Publish complete bytes atomically without replacing an existing
            # file. An interrupted write cannot poison the immutable filename.
            with NamedTemporaryFile(dir=directory, suffix=".partial", delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException:
                    stream.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if path.read_bytes() != content:
                        raise RuntimeError(f"Concurrent immutable output differs: {path}")
            finally:
                temporary.unlink(missing_ok=True)
    return sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", action="append", help="run UUID; repeat for a bounded batch")
    parser.add_argument("--output", type=Path,
                        default=RUNTIME_ROOT / "strategy-optimization-20260930" / "verified_reports_v4")
    args = parser.parse_args()
    completed = 0
    try:
        runs = [str(UUID(value)) for value in (args.run_id or DEFAULT_RUNS)]
        if len(runs) != len(set(runs)) or len(runs) > 20:
            raise ValueError("Use 1-20 unique run IDs per batch")
        output = args.output.resolve()
        if not RUNTIME_ROOT.is_dir() or not output.is_relative_to(RUNTIME_ROOT.resolve()):
            raise RuntimeError("Output must be inside the available D:/TradingML/runtimes root")
        _load_private_credentials()
        with closing(backtest_v4_operator_client_from_env()) as journal, closing(v3_client("read")) as market:
            for index, run_id in enumerate(runs):
                print(f"Runs: active=1 queued={len(runs)-index-1} completed={completed} "
                      f"skipped=0 retried=0 failed=0\nReading {run_id}", flush=True)
                report = build_report(SelectOnly(journal), SelectOnly(market), run_id)
                digest = persist(output / run_id, report)
                completed += 1
                print(f"Verified {len(report['positions'])} positions; SHA256 {digest}\n"
                      f"Saved {output / run_id}", flush=True)
        print(f"Complete: active=0 queued=0 completed={completed} skipped=0 retried=0 failed=0")
        return 0
    except KeyboardInterrupt:
        print(f"Interrupted; {completed} runs persisted. Rerun the same command to verify/resume.", file=sys.stderr)
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

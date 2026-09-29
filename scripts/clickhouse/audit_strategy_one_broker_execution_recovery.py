"""Read-only audit of normalized simulator executions at a V4 checkpoint.

The run may be terminal while the selected broker checkpoint is historical.
No result from this script grants interrupted-run resume admission.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import platform
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_v4_execution_restore import load_v4_broker_executions
from src.trading_runtime.arte_journal_commit_v4 import (
    V4CommittedPrefix, load_verified_v4_prefix,
)
from src.trading_runtime.arte_journal_writer import (
    _literal, _rows, backtest_v4_operator_client_from_env,
    load_typed_run_context,
)
from src.trading_runtime.arte_oms_projection import (
    load_recovered_strategy_one_oms_lineage,
)
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    load_unattested_broker_match_snapshot,
)


def audit(*, run_id: str, checkpoint_sequence: int) -> tuple[int, int]:
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or str(UUID(run_id)) != run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1):
        raise ValueError("Audit needs an exact workstation run and checkpoint")
    credential = Path(r"D:\TradingML\secrets\backtest_v4_runner.env")
    if not credential.is_file():
        raise RuntimeError("Managed journal audit credential is unavailable")
    os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(credential)
    with closing(backtest_v4_operator_client_from_env()) as client:
        context = load_typed_run_context(client, run_id)
        if context["mode"] != "backtest":
            raise RuntimeError("Broker execution audit requires a Backtest run")
        terminal_prefix = load_verified_v4_prefix(client, run_id)
        if terminal_prefix is None or checkpoint_sequence > terminal_prefix.last_sequence:
            raise RuntimeError("Broker execution audit lacks verified V4 prefix")
        matches = _rows(client,
            "SELECT batch_id,source_cursor,status FROM arte.trading_commit_v4 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND last_sequence={checkpoint_sequence} "
            "LIMIT 2 FORMAT JSONEachRow")
        if len(matches) != 1 or matches[0]["batch_id"] not in terminal_prefix.batch_ids:
            raise RuntimeError("Broker checkpoint is not an exact V4 commit boundary")
        position = terminal_prefix.batch_ids.index(matches[0]["batch_id"])
        prefix = V4CommittedPrefix(
            run_id, checkpoint_sequence, matches[0]["batch_id"],
            matches[0]["source_cursor"], matches[0]["status"],
            terminal_prefix.batch_ids[:position + 1])
        broker = load_unattested_broker_match_snapshot(
            client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
        accounts = frozenset(row["account_id"] for row in broker.accounts)
        lineages = load_recovered_strategy_one_oms_lineage(
            client, prefix, allowed_accounts=accounts)
        requests = {}
        broker_ids = {}
        for lineage in lineages:
            for request in lineage.orders:
                if request.cOID in requests:
                    raise RuntimeError("Audit repeats OMS client order")
                requests[request.cOID] = request
            for binding in lineage.state.broker_bindings:
                index = binding["request_index"]
                if index is None:
                    continue
                if type(index) is not int or not 0 <= index < len(lineage.orders):
                    raise RuntimeError("Audit has invalid OMS broker binding")
                broker_id = binding["broker_order_id"]
                if broker_id in broker_ids:
                    raise RuntimeError("Audit repeats OMS broker order")
                broker_ids[broker_id] = lineage.orders[index].cOID
        executions = load_v4_broker_executions(
            client, prefix, requests_by_coid=requests,
            coid_by_broker_id=broker_ids,
            next_execution_id=int(broker.snapshot["next_execution_id"]))
        root = broker.snapshot
        boundary = market_day_boundary(date.fromisoformat(root["session_date"]), 0)
        boundary += timedelta(milliseconds=int(root["boundary_ms"]))
        if any(datetime.fromisoformat(row["trade_time"]).astimezone(timezone.utc)
               > boundary for row in executions):
            raise RuntimeError("Audit found a fill after the broker checkpoint")
        if load_verified_v4_prefix(client, run_id) != terminal_prefix:
            raise RuntimeError("Audit V4 prefix moved across reads")
        return len(executions), len(broker.open_orders)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint-sequence", required=True, type=int)
    args = parser.parse_args()
    fills, orders = audit(run_id=args.run_id,
                          checkpoint_sequence=args.checkpoint_sequence)
    print(f"V4 broker execution audit passed: fills={fills} "
          f"open_orders={orders} writes=0")


if __name__ == "__main__":
    main()

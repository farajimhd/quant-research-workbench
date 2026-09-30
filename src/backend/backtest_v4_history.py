"""Bounded, SELECT-only inventory of normalized Strategy 1 Backtest runs.

An inventory row is not a cold-verified journal or a saved review. The UI
must not offer Review/Resume until those separate read contracts are ready.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.trading_runtime.arte_journal_writer import _literal, _rows
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy


def _utc(value: str) -> str:
    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat()


def load_strategy_one_v4_history(client, *, limit: int = 32) -> list[dict]:
    """List fenced run contexts with bounded, explicitly unaudited commit heads."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("V4 history limit must be between 1 and 100")
    contexts = _rows(client, """
        SELECT r.run_id AS run_id,r.run_month AS run_month,
               r.session_date AS session_date,r.started_at AS started_at,
               r.configuration_hash AS configuration_hash,
               c.strategy_id AS strategy_id,c.strategy_revision AS strategy_revision,
               d.initial_cash AS initial_cash
        FROM arte.trading_run_v1 AS r
        INNER JOIN arte.trading_runtime_config_v1 AS c
          ON r.run_id=c.run_id AND r.run_month=c.run_month
        INNER JOIN arte.trading_run_context_commit_v1 AS f
          ON r.run_id=f.run_id AND r.run_month=f.run_month
        INNER JOIN arte.trading_backtest_definition_v1 AS d
          ON r.run_id=d.run_id AND r.run_month=d.run_month
        INNER JOIN arte.trading_backtest_definition_commit_v1 AS df
          ON d.run_id=df.run_id AND d.run_month=df.run_month
          AND d.content_hash=df.definition_hash
        WHERE r.mode='backtest' AND r.evaluation_interval_ms=100
          AND c.strategy_id={strategy_id} AND c.strategy_revision IN (1,2,3,4)
        ORDER BY r.started_at DESC,r.run_id DESC
        LIMIT {limit_plus_one} FORMAT JSONEachRow
    """.format(strategy_id=_literal(STRATEGY_ID), revision=STRATEGY_NUMBER,
               limit_plus_one=limit + 1))
    if len(contexts) > limit:
        contexts = contexts[:limit]
    ids: list[str] = []
    for row in contexts:
        run_id = str(UUID(str(row["run_id"])))
        if (run_id in ids or not is_numbered_fixed_strategy(str(row["strategy_id"]), int(row["strategy_revision"]))
                or not str(row["session_date"])
                or float(row["initial_cash"]) <= 0):
            raise RuntimeError("V4 Backtest history has duplicate or invalid run context")
        ids.append(run_id)
    if not ids:
        return []
    heads = _rows(client, """
        SELECT run_id,last_sequence,status,committed_at,batch_id
        FROM arte.trading_commit_v4
        WHERE run_id IN ({ids})
        ORDER BY run_id,last_sequence DESC,batch_id DESC
        LIMIT 2 BY run_id FORMAT JSONEachRow
    """.format(ids=",".join(_literal(run_id) for run_id in ids)))
    head_by_id: dict[str, dict] = {}
    for row in heads:
        run_id = str(UUID(str(row["run_id"])))
        if run_id not in ids:
            raise RuntimeError("V4 Backtest history returned an unrequested run")
        prior = head_by_id.get(run_id)
        if prior is not None:
            if int(prior["last_sequence"]) == int(row["last_sequence"]):
                raise RuntimeError("V4 Backtest history has ambiguous commit heads")
            continue
        head_by_id[run_id] = row
    result = []
    for row, run_id in zip(contexts, ids):
        head = head_by_id.get(run_id)
        status = str(head["status"]) if head is not None else "uncommitted"
        if status not in {"running", "completed", "stopped", "failed", "uncommitted"}:
            raise RuntimeError("V4 Backtest history has an unknown commit status")
        result.append({
            "schema_version": 1,
            "run_id": run_id,
            "status": status,
            "mode": "backtest",
            "execution_mode": "100ms",
            "session_date": str(row["session_date"]),
            "created_at": _utc(row["started_at"]),
            "updated_at": _utc(head["committed_at"]) if head else _utc(row["started_at"]),
            "current_time": None,
            "configuration_content_hash": str(row["configuration_hash"]),
            "configuration_label": f"Strategy {row['strategy_revision']}",
            "strategy_id": STRATEGY_ID,
            "strategy_name": f"Strategy {row['strategy_revision']}",
            "strategy_revision": int(row["strategy_revision"]),
            "initial_cash": float(row["initial_cash"]),
            "configuration_revision": int(row["strategy_revision"]),
            "resident": False,
            "journal_backend": "arte_typed_journal_v4",
            "journal_verification": "inventory_only",
            "journal_sequence": int(head["last_sequence"]) if head else 0,
            "review_available": False,
            "v4_review_available": status in {"completed", "stopped", "failed"},
            # Inventory cannot certify a restart anchor. Offer only a
            # verification attempt; the resume endpoint must cold-verify it.
            "resume_attempt_available": status == "running" and head is not None,
            "checkpoint": {"resume_supported": False},
            "tickers": [],
            "processed_events": None,
        })
    return result

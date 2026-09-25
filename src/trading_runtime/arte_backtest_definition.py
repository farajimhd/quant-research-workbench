"""Staged normalized fixed-Backtest definition facts, without saved files.

The shared trading_run_v1 row owns session, interval, configuration hash, and
market-plan token. These tables contain only the missing launch scalars and
ordered membership. Full cold recovery also requires the separately approved
configuration revision and certified market-plan resolver; this module does
not claim either authority or publish rows.
"""
from __future__ import annotations

from datetime import date, time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import math
import re
from typing import Any, Mapping

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ZERO = "0" * 64
DEFINITION = TableContract(
    "trading_backtest_definition_v1",
    (("run_id", "String"), ("run_month", "Date"),
     ("final_session_date", "Date"),
     ("start_local_ms", "UInt32"), ("end_local_ms", "UInt32"),
     ("initial_cash", "Decimal(38, 10)"),
     ("simulation_profile", "LowCardinality(String)"),
     ("activation_delay_us", "UInt32"),
     ("minimum_p_norm", "Decimal(38, 10)"),
     ("configuration_revision_id", "String"),
     ("causal_v7_plan_token", "String"),
     ("structure_book", "String"),
     ("structure_fingerprint", "FixedString(64)"),
     ("ticker_population_mode", "LowCardinality(String)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(run_month)", "run_id",
)
TICKER = TableContract(
    "trading_backtest_ticker_v1",
    (("run_id", "String"), ("run_month", "Date"),
     ("ordinal", "UInt32"), ("ticker", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(run_month)", "run_id, ordinal",
)
ASSIGNMENT = TableContract(
    "trading_backtest_assignment_v1",
    (("run_id", "String"), ("run_month", "Date"),
     ("ordinal", "UInt32"), ("assignment_id", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(run_month)", "run_id, ordinal",
)
COMMIT = TableContract(
    "trading_backtest_definition_commit_v1",
    (("run_id", "String"), ("run_month", "Date"),
     ("definition_hash", "FixedString(64)"),
     ("ticker_count", "UInt32"), ("ticker_hash", "FixedString(64)"),
     ("assignment_count", "UInt32"),
     ("assignment_hash", "FixedString(64)")),
    "toYYYYMM(run_month)", "run_id",
)
TABLES = (DEFINITION, TICKER, ASSIGNMENT, COMMIT)


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sealed(row: Mapping[str, Any]) -> dict[str, Any]:
    content = dict(row)
    return {**content, "content_hash": _digest(content)}


def _decimal(value: Any, *, scale: int = 10) -> str:
    if type(value) not in (int, float, Decimal) or (
        type(value) is float and not math.isfinite(value)
    ):
        raise ValueError("Backtest definition needs finite numeric scalars")
    try:
        number = Decimal(str(value))
        quantum = Decimal(1).scaleb(-scale)
        rounded = number.quantize(quantum)
    except InvalidOperation as exc:
        raise ValueError("Backtest scalar exceeds Decimal width") from exc
    if not number.is_finite() or rounded != number or abs(number) >= Decimal(10) ** (38 - scale):
        raise ValueError("Backtest scalar cannot fit Decimal(38, 10) losslessly")
    return format(rounded, "f")


def _local_ms(value: time) -> int:
    if not isinstance(value, time) or value.tzinfo is not None or value.microsecond % 1000:
        raise ValueError("Backtest local clock needs exact millisecond resolution")
    return ((value.hour * 60 + value.minute) * 60 + value.second) * 1000 + value.microsecond // 1000


def _canonical_stored_row(table: TableContract, row: Mapping[str, Any]) -> dict[str, Any]:
    if set(row) != {name for name, _ in table.columns}:
        raise ValueError(f"Backtest {table.name} has missing or extra columns")
    result = {}
    for name, kind in table.columns:
        value = row[name]
        if kind == "Decimal(38, 10)":
            if not isinstance(value, (str, int, float)):
                raise ValueError(f"Backtest {table.name}.{name} is not decimal")
            result[name] = _decimal(Decimal(str(value)))
        elif kind == "Date":
            result[name] = date.fromisoformat(str(value)).isoformat()
        elif kind.startswith("UInt"):
            bits = int(kind[4:])
            if type(value) is not int or not 0 <= value < 1 << bits:
                raise ValueError(f"Backtest {table.name}.{name} exceeds UInt{bits}")
            result[name] = value
        elif kind == "FixedString(64)":
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise ValueError(f"Backtest {table.name}.{name} is not SHA-256")
            result[name] = value
        elif kind in {"String", "LowCardinality(String)"}:
            if not isinstance(value, str):
                raise ValueError(f"Backtest {table.name}.{name} is not text")
            result[name] = value
        else:
            raise ValueError(f"Unsupported Backtest definition column: {table.name}.{name}")
    return result


def prepare_backtest_definition(
    run_id: str, definition: Any,
) -> dict[str, Any]:
    """Project only fixed Backtest launch facts missing from trading_run_v1."""
    from src.backend.backtest_market_data import ExecutionInterval
    from src.backend.replay_run_service import ReplayRunDefinition, RunMode

    if (not isinstance(definition, ReplayRunDefinition)
            or definition.mode != RunMode.BACKTEST
            or definition.prepare_frames_only or definition.debug_fixture is not None
            or not isinstance(run_id, str) or not run_id):
        raise ValueError("Typed definition requires a normal fixed Backtest run")
    interval = ExecutionInterval.parse(definition.execution_interval)
    if interval.kind != "fixed":
        raise ValueError("Typed definition requires a fixed evaluation interval")
    revision = definition.configuration_revision
    revision_id = str(revision.get("revision_id") or "")
    revision_hash = str(revision.get("content_hash") or "")
    if (not revision_id or not _HEX.fullmatch(revision_hash)
            or not definition.market_data_plan.get("token")
            or (definition.causal_v7_plan and not definition.causal_v7_plan.get("token"))):
        raise ValueError("Typed definition lacks pinned configuration or market authority")
    final_date = definition.final_session_date or definition.session_date
    if not isinstance(final_date, date):
        raise ValueError("Typed definition lacks a final session date")
    delay = Decimal(str(definition.new_order_activation_delay_ms)) * 1000
    if not delay.is_finite() or delay != delay.to_integral_value() or not 0 <= delay < 2**32:
        raise ValueError("Typed activation delay is not exact microseconds")
    fingerprint = definition.experimental_structure_fingerprint or _ZERO
    if (not _HEX.fullmatch(fingerprint)
            or (definition.experimental_structure_book == "level-book-v7"
                and (fingerprint == _ZERO or not definition.causal_v7_plan.get("token")))):
        raise ValueError("Typed structure authority is incomplete or not SHA-256")
    month = definition.session_date.replace(day=1).isoformat()
    common = {"run_id": run_id, "run_month": month}
    parent = _sealed({
        **common, "final_session_date": final_date.isoformat(),
        "start_local_ms": _local_ms(definition.start_time),
        "end_local_ms": _local_ms(definition.end_time),
        "initial_cash": _decimal(definition.initial_cash),
        "simulation_profile": definition.simulation_profile,
        "activation_delay_us": int(delay),
        "minimum_p_norm": _decimal(definition.minimum_p_norm),
        "configuration_revision_id": revision_id,
        "causal_v7_plan_token": str(definition.causal_v7_plan.get("token") or ""),
        "structure_book": definition.experimental_structure_book,
        "structure_fingerprint": fingerprint,
        "ticker_population_mode": "explicit" if definition.tickers else "market_plan",
    })
    tickers = tuple(_sealed({**common, "ordinal": index, "ticker": ticker})
                    for index, ticker in enumerate(definition.tickers))
    assignments = tuple(_sealed({**common, "ordinal": index,
                                 "assignment_id": assignment_id})
                        for index, assignment_id in enumerate(definition.assignment_ids))
    if (len(tickers) >= 2**32 or len(assignments) >= 2**32
            or len({row["ticker"] for row in tickers}) != len(tickers)
            or len({row["assignment_id"] for row in assignments}) != len(assignments)
            or any(not row["assignment_id"] for row in assignments)):
        raise ValueError("Typed Backtest membership is too large or duplicated")
    commit = {
        **common, "definition_hash": parent["content_hash"],
        "ticker_count": len(tickers),
        "ticker_hash": _digest([(row["ordinal"], row["content_hash"])
                                for row in tickers]),
        "assignment_count": len(assignments),
        "assignment_hash": _digest([(row["ordinal"], row["content_hash"])
                                    for row in assignments]),
    }
    return {"definition": parent, "tickers": tickers,
            "assignments": assignments, "commit": commit}


def verify_backtest_definition_rows(
    *, definitions: tuple[Mapping[str, Any], ...],
    tickers: tuple[Mapping[str, Any], ...],
    assignments: tuple[Mapping[str, Any], ...],
    commits: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Cold-verify one exact immutable parent and its ordered child population."""
    if len(definitions) != 1 or len(commits) != 1:
        raise ValueError("Backtest definition lacks one parent and commit")
    parent = _canonical_stored_row(DEFINITION, definitions[0])
    tickers = tuple(sorted(
        (_canonical_stored_row(TICKER, row) for row in tickers),
        key=lambda row: row["ordinal"],
    ))
    assignments = tuple(sorted(
        (_canonical_stored_row(ASSIGNMENT, row) for row in assignments),
        key=lambda row: row["ordinal"],
    ))
    commit = _canonical_stored_row(COMMIT, commits[0])
    for table, rows in ((DEFINITION, (parent,)), (TICKER, tickers),
                        (ASSIGNMENT, assignments), (COMMIT, (commit,))):
        for row in rows:
            if (row["run_id"] != parent["run_id"]
                    or str(row["run_month"]) != str(parent["run_month"])):
                raise ValueError("Backtest definition child identity differs")
            if table is not COMMIT:
                content = {key: value for key, value in row.items()
                           if key != "content_hash"}
                if _digest(content) != row["content_hash"]:
                    raise ValueError(f"Backtest {table.name} content hash differs")
    if (len(tickers) >= 2**32 or len(assignments) >= 2**32
            or [int(row["ordinal"]) for row in tickers] != list(range(len(tickers)))
            or [int(row["ordinal"]) for row in assignments] != list(range(len(assignments)))
            or len({row["ticker"] for row in tickers}) != len(tickers)
            or len({row["assignment_id"] for row in assignments}) != len(assignments)
            or any(not row["assignment_id"] for row in assignments)
            or parent["ticker_population_mode"] != (
                "explicit" if tickers else "market_plan")):
        raise ValueError("Backtest definition membership differs")
    if (not 4 * 60 * 60 * 1000 <= parent["start_local_ms"] <= 20 * 60 * 60 * 1000
            or not 4 * 60 * 60 * 1000 <= parent["end_local_ms"] <= 20 * 60 * 60 * 1000
            or not Decimal("1000") <= Decimal(parent["initial_cash"]) <= Decimal("1000000000")
            or parent["simulation_profile"] not in {"baseline", "stress"}
            or parent["activation_delay_us"] > 60_000_000
            or not Decimal("0") <= Decimal(parent["minimum_p_norm"]) <= Decimal("1")
            or (parent["structure_book"] == "level-book-v7"
                and parent["structure_fingerprint"] == _ZERO)):
        raise ValueError("Backtest definition scalar contract differs")
    expected = {
        "run_id": parent["run_id"], "run_month": parent["run_month"],
        "definition_hash": parent["content_hash"],
        "ticker_count": len(tickers),
        "ticker_hash": _digest([(row["ordinal"], row["content_hash"])
                                for row in tickers]),
        "assignment_count": len(assignments),
        "assignment_hash": _digest([(row["ordinal"], row["content_hash"])
                                    for row in assignments]),
    }
    if commit != expected:
        raise ValueError("Backtest definition commit differs from its rows")
    return {"definition": parent, "tickers": tickers,
            "assignments": assignments, "commit": commit}


def load_backtest_definition(
    client: Any, run_id: str, *, run_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Read only a fenced definition consistent with the shared run context."""
    from src.trading_runtime.arte_journal_writer import _literal, _rows

    if (not isinstance(run_id, str) or not run_id
            or run_context.get("run_id") != run_id
            or run_context.get("mode") != "backtest"):
        raise ValueError("Backtest definition needs a verified shared run context")
    families = {}
    for key, table in (("definitions", DEFINITION), ("tickers", TICKER),
                       ("assignments", ASSIGNMENT), ("commits", COMMIT)):
        columns = ",".join(name for name, _ in table.columns)
        families[key] = tuple(_rows(client,
            f"SELECT {columns} FROM arte.{table.name} "
            f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow"))
    verified = verify_backtest_definition_rows(**families)
    parent = verified["definition"]
    if (parent["run_month"] != str(run_context.get("run_month"))
            or date.fromisoformat(parent["final_session_date"])
            < date.fromisoformat(str(run_context.get("session_date")))
            or (parent["final_session_date"] == str(run_context.get("session_date"))
                and parent["end_local_ms"] < parent["start_local_ms"])
            or (run_context.get("evaluation_interval_ms") is None)
            or not run_context.get("market_plan_token")
            or not _HEX.fullmatch(str(run_context.get("configuration_hash") or ""))):
        raise RuntimeError("Backtest definition differs from shared run authority")
    return verified

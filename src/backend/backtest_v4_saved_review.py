"""Cold-verified, disk-free terminal evidence for immutable Strategy 1.

This is a bounded normalized-journal page, not a fabricated legacy Canvas
controller or a resumable execution state. JSON is only the API transport.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from src.trading_runtime.arte_backtest_snapshot_anchor import (
    load_terminal_backtest_snapshot,
)
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _committed_batch_filter, _literal, _rows, load_committed_commission_page,
    load_committed_execution_page, load_committed_order_command_page,
    load_committed_order_transition_page, load_typed_run_context,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.backend.backtest_terminal_v2_fence import _verify_rows, _verify_v1_rows
from src.backend.typed_backtest_review_core import (
    AuditedSessionCache, _cache_key, _client_scope, _head_matches,
)


_V4_CACHE = AuditedSessionCache(max_sessions=8, max_bytes=8 * 1024 * 1024,
                                max_entry_bytes=512 * 1024, ttl_seconds=300)
_V4_PERFORMANCE_CACHE = AuditedSessionCache(
    max_sessions=8, max_bytes=16 * 1024 * 1024,
    max_entry_bytes=2 * 1024 * 1024, ttl_seconds=300,
)


def _terminal_financial_accounts(client, prefix, account_ids: tuple[str, ...]) -> dict:
    table = "trading_backtest_account_snapshot_v2"
    columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{table} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND batch_id=toUUID({_literal(prefix.last_batch_id)}) "
        f"LIMIT {len(account_ids) + 1} FORMAT JSONEachRow")
    # JSONEachRow renders an integral Float64 (notably 0.0) as JSON 0. Its
    # Python representation is int even though the column authority is Float64.
    float_fields = tuple(name for name, kind in _CONTRACTS[table].columns
                         if kind == "Float64")
    canonical_rows = []
    for row in rows:
        if any(type(row.get(name)) not in (int, float) for name in float_fields):
            raise RuntimeError("Saved review financial Float64 wire value is invalid")
        canonical_rows.append({**row, **{
            name: float(row[name]) for name in float_fields
        }})
    verified = _verify_rows(table, tuple(canonical_rows))
    if (len(verified) != len(account_ids)
            or {row["account_id"] for row in verified} != set(account_ids)
            or any(row["run_id"] != prefix.run_id
                   or row["batch_id"] != prefix.last_batch_id for row in verified)):
        raise RuntimeError("Saved review terminal financial accounts differ from run")
    return {row["account_id"]: {
        key: row[key] for key in (
            "source_timestamp_ms", "currency", "net_liquidation",
            "total_cash_value", "buying_power", "gross_position_value",
            "available_funds", "excess_liquidity", "expected_position_count",
        )
    } for row in verified}


def _terminal_attestation(client, normalized: str,
                          cache: AuditedSessionCache | None) -> dict:
    """Share one cold-audited V4 terminal head across bounded Canvas reads."""
    selected_cache = cache if cache is not None else _V4_CACHE
    context = load_typed_run_context(client, normalized)
    if (context["mode"] != "backtest"
            or context["strategy_id"] != STRATEGY_ID
            or int(context["strategy_revision"]) != STRATEGY_NUMBER
            or context["evaluation_interval_ms"] != 100):
        raise ValueError("Saved review accepts only immutable Strategy 1 at 100 ms")
    attestation = None
    for key in selected_cache.candidate_keys(_client_scope(client), normalized):
        candidate = selected_cache.get(key)
        if (candidate is not None and candidate["context"] == context
                and _head_matches(client, normalized, candidate["prefix"])):
            attestation = candidate
            break
    if attestation is None:
        prefix = load_verified_v4_prefix(client, normalized)
        if prefix is None or prefix.status not in {"completed", "stopped", "failed"}:
            raise ValueError("Saved review requires a cold-verified terminal V4 run")
        accounts = {
            account_id: load_terminal_backtest_snapshot(
                client, prefix, account_id=account_id)
            for account_id in context["account_ids"]
        }
        cursor = load_latest_backtest_cursor(client, prefix)
        if (cursor is None and prefix.source_cursor != "start"
                or cursor is not None and (
                    str(cursor["session_date"]) != str(context["session_date"])
                    or prefix.source_cursor !=
                    f"{cursor['session_date']}:{int(cursor['boundary_ms'])}")):
            raise RuntimeError("Saved review market cursor differs from terminal run")
        if not _head_matches(client, normalized, prefix):
            raise RuntimeError("Saved review terminal head changed during audit")
        attestation = {
            "context": context, "prefix": prefix, "cursor": cursor,
            "financial_accounts": _terminal_financial_accounts(
                client, prefix, tuple(context["account_ids"])),
            "accounts": {
                account_id: {
                    "state_hash": snapshot["state_hash"],
                    "state_revision": snapshot["state_revision"],
                    "snapshot_at": snapshot["snapshot_at"],
                }
                for account_id, snapshot in accounts.items()
            },
        }
        selected_cache.put(_cache_key(client, normalized, context, prefix),
                           attestation)
    return attestation


def load_v4_terminal_review_page(client, run_id: str, *,
                                 after_sequence: int = 0,
                                 limit: int = 250,
                                 cache: AuditedSessionCache | None = None) -> dict:
    """Cold-audit once; recheck the terminal head before each bounded page."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 review requires a UUID run id") from exc
    if (type(after_sequence) is not int or after_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000
            or cache is not None and not isinstance(cache, AuditedSessionCache)):
        raise ValueError("Strategy 1 review page bounds are invalid")
    attestation = _terminal_attestation(client, normalized, cache)
    context = attestation["context"]
    prefix = attestation["prefix"]
    cursor = attestation["cursor"]
    if after_sequence > prefix.last_sequence:
        raise ValueError("Saved review cursor exceeds the verified journal")
    page = load_typed_event_page(
        client, prefix, after_sequence=after_sequence, limit=limit)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved review terminal head changed during page read")
    next_sequence = int(page[-1].event["sequence"]) if page else after_sequence
    return {
        "schema_version": "strategy-one-v4-terminal-review-page-v1",
        "run": context,
        "status": prefix.status,
        "verified_sequence": prefix.last_sequence,
        "market_cursor": cursor,
        "market_cursor_verified": cursor is not None,
        "limitations": (["This archived V4 run has no persisted market-boundary cursor; "
                         "its exact processed-through clock is unavailable."]
                        if cursor is None else []),
        "accounts": attestation["accounts"],
        "financial_accounts": attestation["financial_accounts"],
        "events": tuple({
            "event": row.event,
            "detail_family": row.detail_family,
            "detail": row.detail,
        } for row in page),
        "next_sequence": next_sequence,
        "complete": next_sequence == prefix.last_sequence,
        "resume_supported": False,
    }


def load_v4_trade_history_page(client, run_id: str, *,
                               after_fill_sequence: int = 0,
                               after_commission_sequence: int = 0,
                               limit: int = 250,
                               cache: AuditedSessionCache | None = None) -> dict:
    """Read real fills and fee revisions for the certified Canvas, never legacy state."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 trade history requires a UUID run id") from exc
    if (type(after_fill_sequence) is not int or after_fill_sequence < 0
            or type(after_commission_sequence) is not int
            or after_commission_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000
            or cache is not None and not isinstance(cache, AuditedSessionCache)):
        raise ValueError("Strategy 1 trade history page bounds are invalid")
    attestation = _terminal_attestation(client, normalized, cache)
    prefix = attestation["prefix"]
    if max(after_fill_sequence, after_commission_sequence) > prefix.last_sequence:
        raise ValueError("Trade history cursor exceeds the verified journal")
    fills = load_committed_execution_page(
        client, prefix, after_sequence=after_fill_sequence, limit=limit)
    commissions = load_committed_commission_page(
        client, prefix, after_sequence=after_commission_sequence, limit=limit)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Trade history terminal head changed during page read")
    return {
        "schema_version": "strategy-one-v4-trade-history-page-v1",
        "run_id": normalized,
        "status": prefix.status,
        "verified_sequence": prefix.last_sequence,
        "fills": fills,
        "commissions": commissions,
        "next_fill_sequence": (int(fills[-1]["sequence"])
                               if fills else after_fill_sequence),
        "next_commission_sequence": (int(commissions[-1]["sequence"])
                                     if commissions else after_commission_sequence),
        "complete": len(fills) < limit and len(commissions) < limit,
    }


def load_v4_order_history_page(client, run_id: str, *,
                               after_command_sequence: int = 0,
                               after_transition_sequence: int = 0,
                               limit: int = 250,
                               cache: AuditedSessionCache | None = None) -> dict:
    """Read committed order commands and transitions without inventing state."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 order history requires a UUID run id") from exc
    if (type(after_command_sequence) is not int or after_command_sequence < 0
            or type(after_transition_sequence) is not int
            or after_transition_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError("Strategy 1 order history page bounds are invalid")
    prefix = _terminal_attestation(client, normalized, cache)["prefix"]
    if max(after_command_sequence, after_transition_sequence) > prefix.last_sequence:
        raise ValueError("Order history cursor exceeds the verified journal")
    commands = load_committed_order_command_page(
        client, prefix, after_sequence=after_command_sequence, limit=limit)
    transitions = load_committed_order_transition_page(
        client, prefix, after_sequence=after_transition_sequence, limit=limit)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Order history terminal head changed during page read")
    return {
        "schema_version": "strategy-one-v4-order-history-page-v1",
        "run_id": normalized,
        "verified_sequence": prefix.last_sequence,
        "commands": commands,
        "transitions": transitions,
        "next_command_sequence": (int(commands[-1]["sequence"])
                                  if commands else after_command_sequence),
        "next_transition_sequence": (int(transitions[-1]["sequence"])
                                     if transitions else after_transition_sequence),
        "complete": len(commands) < limit and len(transitions) < limit,
    }


def _utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _complete_detail_rows(loader, client, prefix, *, maximum: int = 100_000) -> tuple[dict, ...]:
    """Bound every read, and refuse to present a partial performance report."""
    rows: list[dict] = []
    after = 0
    while True:
        page = loader(client, prefix, after_sequence=after, limit=1000)
        if len(rows) + len(page) > maximum:
            raise RuntimeError("Saved Canvas performance exceeds the bounded read limit")
        rows.extend(page)
        if len(page) < 1000:
            return tuple(rows)
        next_after = int(page[-1]["sequence"])
        if next_after <= after:
            raise RuntimeError("Saved Canvas performance cursor did not advance")
        after = next_after


def _saved_protection_events(client, prefix, *, maximum: int = 20_000) -> list[dict]:
    """Read only committed, sealed protection facts for chart presentation.

    Unlike a whole-journal scan, this projects the normalized protection family
    directly. It is never a broker/recovery authority and never writes ARTE.
    """
    from src.backend.backtest_protection_change_v3 import recover_protection_change_payload

    def fetch(table: str, predicate: str, bound: int) -> tuple[dict, ...]:
        columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
        raw = _rows(client, f"SELECT {columns} FROM arte.{table} "
                    f"WHERE run_id={_literal(prefix.run_id)} {predicate} "
                    f"{_committed_batch_filter(prefix)}"
                    f"LIMIT {bound + 1} FORMAT JSONEachRow")
        if len(raw) > bound:
            raise RuntimeError("Saved chart protection evidence exceeds its bound")
        return _verify_v1_rows(table, tuple(raw))

    details = fetch("trading_protection_change_v3", "", maximum)
    if not details:
        return []
    child_count = sum(int(row["entry_order_count"]) for row in details)
    if child_count > maximum * 16:
        raise RuntimeError("Saved chart protection child evidence exceeds its bound")
    children: list[dict] = []
    parents: list[dict] = []
    # Bound the SQL text as well as returned rows; a long all-ticker session
    # must not create a single unbounded IN expression on the read path.
    for start in range(0, len(details), 500):
        group = details[start:start + 500]
        ids = ",".join(f"toUUID({_literal(str(UUID(row['record_id'])))})" for row in group)
        predicate = f"AND record_id IN ({ids})"
        children.extend(fetch("trading_protection_entry_order_v3", predicate,
                              sum(int(row["entry_order_count"]) for row in group)))
        parents.extend(fetch("trading_event_v1", predicate, len(group)))
    if len(parents) != len(details) or len(children) != child_count:
        raise RuntimeError("Saved chart protection evidence is incomplete")
    parent_by_id = {str(UUID(row["record_id"])): row for row in parents}
    if len(parent_by_id) != len(parents):
        raise RuntimeError("Saved chart protection parent repeats")
    children_by_id: dict[str, list[dict]] = {}
    for row in children:
        children_by_id.setdefault(str(UUID(row["record_id"])), []).append(row)
    events = []
    for detail in details:
        identity = str(UUID(detail["record_id"]))
        parent = parent_by_id.pop(identity, None)
        if parent is None or int(parent["sequence"]) > prefix.last_sequence:
            raise RuntimeError("Saved chart protection parent is uncommitted")
        ordered = sorted(children_by_id.pop(identity, ()), key=lambda row: int(row["ordinal"]))
        payload = recover_protection_change_payload(parent, detail, ordered)
        if (detail["batch_id"] != parent["batch_id"]
                or detail["event_month"] != parent["event_month"]
                or detail["account_id"] != parent["account_id"]):
            raise RuntimeError("Saved chart protection identity differs")
        events.append({**payload, "account_id": parent["account_id"],
                       "event_time": _utc_timestamp(parent["event_time"]).isoformat(),
                       "sequence": int(parent["sequence"])})
    if parent_by_id or children_by_id:
        raise RuntimeError("Saved chart protection has orphan evidence")
    return events


def load_v4_performance_report(client, run_id: str, *,
                               cache: AuditedSessionCache | None = None) -> dict:
    """Derive the existing flat-to-flat report from complete normalized facts.

    This is a read-only presentation projection, not a journal or market writer.
    Fees must be final for every fill before net P&L can be shown.
    """
    from src.trading_runtime.domain import Execution, InstrumentContract
    from src.trading_runtime.performance import (
        build_performance_report, derive_position_lifecycles,
        derive_trade_episodes,
    )
    from src.trading_runtime.protection_timeline import attach_protection_timelines

    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 performance requires a UUID run id") from exc
    prefix = _terminal_attestation(client, normalized, cache)["prefix"]
    fills = _complete_detail_rows(load_committed_execution_page, client, prefix)
    fees = _complete_detail_rows(load_committed_commission_page, client, prefix)
    fee_by_execution: dict[str, dict] = {}
    for fee in fees:
        identity = str(fee["execution_id"])
        if identity not in fee_by_execution or int(fee["sequence"]) > int(fee_by_execution[identity]["sequence"]):
            fee_by_execution[identity] = fee
    executions = []
    identities = set()
    journal_sequences: set[int] = set()
    for fill in fills:
        identity = str(fill["execution_id"])
        if identity in identities:
            raise RuntimeError("Saved Canvas performance repeats an execution identity")
        identities.add(identity)
        fee = fee_by_execution.get(identity)
        if fee is None or str(fee["status"]).lower() != "final":
            raise RuntimeError("Saved Canvas performance requires final fees for every fill")
        if fee["account_id"] != fill["account_id"]:
            raise RuntimeError("Saved Canvas fee account differs from its fill")
        if fee["currency"] != fill["currency"]:
            raise RuntimeError("Saved Canvas performance requires fee and fill currency parity")
        side = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}.get(str(fill["side"]).upper())
        if side is None:
            raise RuntimeError("Saved Canvas fill has an unsupported side")
        conid = int(fill["conid"])
        if conid <= 0:
            raise RuntimeError("Saved Canvas performance requires point-in-time conid on every fill")
        symbol = str(fill["ticker"])
        stamp = _utc_timestamp(fill["source_event_time"])
        sequence = fill.get("sequence")
        if type(sequence) is not int or sequence < 1 or sequence in journal_sequences:
            raise RuntimeError("Saved Canvas fill lacks a unique committed journal sequence")
        journal_sequences.add(sequence)
        executions.append(Execution(
            execution_id=identity, account_id=str(fill["account_id"]),
            instrument=InstrumentContract(
                instrument_id=f"conid:{conid}",
                conid=conid, symbol=symbol, security_type="STK",
                currency=str(fill["currency"]), exchange=str(fill["exchange"]) or "SMART"),
            side=side, quantity=Decimal(str(fill["quantity"])),
            price=Decimal(str(fill["price"])),
            source_event_time=stamp,
            broker_order_id=str(fill["broker_order_id"]),
            client_order_id=str(fill["client_order_id"]),
            exchange=str(fill["exchange"]),
            commission=Decimal(str(fee["commission"])),
            commission_currency=str(fee["currency"]),
            commission_status="final", strategy_id=str(fill["strategy_id"]),
            strategy_revision=int(fill["strategy_revision"]),
            run_id=normalized, setup=str(fill["setup"]),
            exit_reason=str(fill["exit_reason"]),
            signal_price=(Decimal(str(fill["signal_price"]))
                          if fill["signal_price"] is not None else None),
            arrival_midpoint=(Decimal(str(fill["arrival_midpoint"]))
                              if fill["arrival_midpoint"] is not None else None),
            planned_risk=(Decimal(str(fill["planned_risk"]))
                          if fill["planned_risk"] is not None else None),
            journal_sequence=sequence,
        ))
    if set(fee_by_execution) != identities:
        raise RuntimeError("Saved Canvas contains a commission without a matching fill")
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved Canvas terminal head changed during performance projection")
    episodes = derive_trade_episodes(executions)
    report = build_performance_report(episodes, executions, ())
    lifecycles = derive_position_lifecycles(executions, ())
    protection_events = _saved_protection_events(client, prefix)
    # Opening-order identities, not ticker/price coincidence, assign broker
    # protection revisions to a lifecycle. Unmatched events remain journal
    # evidence but cannot be drawn as position-specific rails.
    attach_protection_timelines(
        lifecycles, protection_events, executions, datetime.max.replace(tzinfo=UTC))
    executions_by_id = {execution.execution_id: execution for execution in executions}
    for lifecycle in lifecycles:
        # A stop/target label requires the exact closing broker order to have
        # been effective before its fill. Price proximity is never evidence.
        closing_at = lifecycle.get("closed_at")
        if not closing_at or lifecycle.get("exit_reason"):
            continue
        closing_time = _utc_timestamp(closing_at)
        opening_side = "BUY" if lifecycle["side"] == "LONG" else "SELL"
        exit_executions = [executions_by_id[str(identity)] for identity in lifecycle["execution_ids"]
                           if str(identity) in executions_by_id
                           and executions_by_id[str(identity)].side != opening_side]
        terminal = [execution for execution in exit_executions
                    if execution.source_event_time == closing_time]
        if not terminal:
            continue
        kinds = set()
        first_fill_by_order = {}
        for execution in exit_executions:
            previous = first_fill_by_order.get(execution.broker_order_id)
            if previous is None or execution.journal_sequence < previous.journal_sequence:
                first_fill_by_order[execution.broker_order_id] = execution
        for order_id in {execution.broker_order_id for execution in terminal}:
            execution = first_fill_by_order[order_id]
            states = [event for event in lifecycle["protection_timeline"]
                      if event["phase"] == "effective"
                      and event["order_id"] == execution.broker_order_id
                      and (datetime.fromisoformat(event["event_time"]), int(event["sequence"]))
                      <= (execution.source_event_time, execution.journal_sequence)]
            if not states:
                break
            latest = max(states, key=lambda event: (event["event_time"], event["sequence"]))
            if not latest["active"]:
                break
            kinds.add(latest["kind"])
        else:
            if len(kinds) == 1:
                lifecycle["presentation_exit_reason"] = (
                    "stop_hit" if kinds == {"stop"} else "target_hit")
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved Canvas terminal head changed during chart projection")
    # No order lifecycle projection has been asserted yet. Do not turn an
    # absent order reader into a false zero order count or rejection count.
    report["execution"]["order_count"] = None
    report["execution"]["rejected_order_count"] = None
    return {
        "schema_version": "strategy-one-v4-performance-report-v1",
        "run_id": normalized,
        "verified_sequence": prefix.last_sequence,
        "report": report,
        "position_lifecycles": lifecycles,
        "fill_count": len(executions),
        "fee_count": len(fee_by_execution),
    }


def load_cached_v4_performance_report(client, run_id: str, *,
                                      cache: AuditedSessionCache | None = None) -> dict:
    """Reuse only a fully verified terminal projection, never its authority.

    The attestation rechecks the ClickHouse run context and committed head on
    every call. The bounded cache stores presentation data only in process
    memory; a changed head cannot authorize an old report.
    """
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 performance requires a UUID run id") from exc
    selected_cache = cache if cache is not None else _V4_PERFORMANCE_CACHE
    if not isinstance(selected_cache, AuditedSessionCache):
        raise ValueError("Strategy 1 performance cache is invalid")
    attestation = _terminal_attestation(client, normalized, None)
    prefix = attestation["prefix"]
    key = _cache_key(client, normalized, attestation["context"], prefix)
    cached = selected_cache.get(key)
    if cached is not None and _head_matches(client, normalized, prefix):
        return cached["report"]
    report = load_v4_performance_report(client, normalized)
    if (report["run_id"] != normalized
            or int(report["verified_sequence"]) != prefix.last_sequence
            or not _head_matches(client, normalized, prefix)):
        raise RuntimeError("Saved performance head changed during projection")
    try:
        selected_cache.put(key, {"report": report})
    except ValueError:
        # A large but valid report remains readable without growing the cache.
        pass
    return report

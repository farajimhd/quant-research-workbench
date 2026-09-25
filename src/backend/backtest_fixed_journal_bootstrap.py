"""Inactive, read-only fixed Backtest journal assembly.

The caller must provide an already committed run context and a separately
certified all-family projector. No run, table, or journal row is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable
from uuid import UUID

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_terminal_v2_keeper import FixedTerminalKeeperAuthority
from src.backend.backtest_terminal_v2_preflight import (
    terminal_v2_keeper_proof_preflight, terminal_v2_operator_preflight,
)
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.arte_journal_schema import (
    fixed_backtest_v2_contracts, missing_fixed_backtest_v2_tables,
    storage_preflight,
)
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, load_typed_run_context,
)


@dataclass(frozen=True, slots=True)
class FixedJournalPreflightToken:
    run_id: str
    account_ids: tuple[str, ...]
    run_month: date
    configuration_hash: str
    market_plan_token: str
    projection_certificate: str


@dataclass(frozen=True, slots=True)
class FixedJournalAssembly:
    journal: BacktestMemoryJournal
    writer: ArteJournalWriter
    publisher: BacktestTypedJournalPublisher
    terminal_authority: FixedTerminalKeeperAuthority


def fixed_journal_operator_check(client: Any) -> dict[str, Any]:
    """Read-only, actionable fixed-V2 journal readiness for UI preflight."""
    missing = missing_fixed_backtest_v2_tables(client)
    if missing:
        return {
            "id": "fixed_journal_authority",
            "label": "Normalized ClickHouse trading journal",
            "status": "blocked", "required": True,
            "summary": f"{len(missing)} required normalized arte journal tables are absent",
            "evidence": {"missing_tables": list(missing)},
        }
    terminal_v2_operator_preflight(client)
    return {
        "id": "fixed_journal_authority",
        "label": "Normalized ClickHouse trading journal",
        "status": "ready", "required": True,
        "summary": "Exact typed layout, SSD placement, and narrow grants verified",
        "evidence": {"missing_tables": []},
    }


def prepare_fixed_journal_token(
    read_client: Any, terminal_client: Any, keeper: Any, *, run_id: str,
    account_ids: tuple[str, ...], configuration_hash: str,
    market_plan_token: str,
    projection_certifier: Callable[[], str] | None = None,
) -> FixedJournalPreflightToken:
    """Read-only admission to a pre-published typed run; fail on any mismatch."""
    if (read_client is terminal_client or projection_certifier is None
            or not configuration_hash or not market_plan_token
            or not account_ids or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Fixed journal lacks pinned projection/run authority")
    storage_preflight(read_client, tables=fixed_backtest_v2_contracts())
    terminal_v2_operator_preflight(terminal_client)
    terminal_v2_keeper_proof_preflight(keeper)
    context = load_typed_run_context(read_client, run_id)
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != account_ids
            or context["configuration_hash"] != configuration_hash
            or context["market_plan_token"] != market_plan_token):
        raise RuntimeError("Fixed journal run context differs from pinned authority")
    month = date.fromisoformat(context["run_month"])
    if month.day != 1:
        raise RuntimeError("Fixed journal run month is invalid")
    if load_typed_run_context(terminal_client, run_id) != context:
        raise RuntimeError("Terminal writer observes a different typed run context")
    projection_certificate = projection_certifier()
    if (not isinstance(projection_certificate, str)
            or re.fullmatch(r"[0-9a-f]{64}", projection_certificate) is None):
        raise RuntimeError("Fixed journal projector cannot certify every emitted family")
    return FixedJournalPreflightToken(
        run_id, account_ids, month, configuration_hash,
        market_plan_token, projection_certificate)


def assemble_fixed_journal(
    read_client: Any, writer_client: Any, terminal_client: Any,
    keeper: Any, token: FixedJournalPreflightToken, *,
    attempt_id: str, expected_config: dict[str, Any],
    fixed_market_parent_plan: object, fixed_market_execution_plan: object,
    expected_market_start: datetime, writer_factory: Callable[..., ArteJournalWriter],
    batch_size: int = 512, queue_capacity: int = 8,
) -> FixedJournalAssembly:
    """Construct a bounded lane without attaching it to the active engine."""
    if (len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or not isinstance(token, FixedJournalPreflightToken)
            or re.fullmatch(r"[0-9a-f]{64}", token.projection_certificate) is None
            or not 1 <= batch_size <= 4096
            or not 1 <= queue_capacity <= 64
            or expected_market_start.tzinfo is None
            or fixed_market_parent_plan is None
            or fixed_market_execution_plan is None):
        raise ValueError("Fixed journal bootstrap lacks bounded certified inputs")
    UUID(attempt_id)
    context = load_typed_run_context(read_client, token.run_id)
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != token.account_ids
            or context["configuration_hash"] != token.configuration_hash
            or context["market_plan_token"] != token.market_plan_token):
        raise RuntimeError("Fixed journal context changed before assembly")
    if load_typed_run_context(terminal_client, token.run_id) != context:
        raise RuntimeError("Terminal writer observes a different typed run context")
    if load_typed_run_context(writer_client, token.run_id) != context:
        raise RuntimeError("Batch writer observes a different typed run context")
    journal = BacktestMemoryJournal(run_id=token.run_id)
    try:
        writer = writer_factory(
            writer_client, run_id=token.run_id, capacity=queue_capacity,
            max_events_per_commit=batch_size, coalesce_batches=False,
            journal_profile="backtest_v2")
    except BaseException:
        journal.close()
        raise
    try:
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=attempt_id, run_month=token.run_month,
            batch_size=batch_size, expected_config=expected_config,
            fixed_market_parent_plan=fixed_market_parent_plan,
            fixed_market_execution_plan=fixed_market_execution_plan,
            expected_market_start=expected_market_start)
        authority = FixedTerminalKeeperAuthority(
            keeper=keeper, client=terminal_client, run_id=token.run_id,
            account_ids=token.account_ids)
        return FixedJournalAssembly(journal, writer, publisher, authority)
    except BaseException:
        writer.close()
        journal.close()
        raise

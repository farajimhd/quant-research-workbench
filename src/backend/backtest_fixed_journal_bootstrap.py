"""Inactive, read-only fixed Backtest journal assembly.

The caller must provide an already committed run context and a separately
certified all-family projector. No run, table, or journal row is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Any, Callable
from uuid import UUID

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_squeeze_episode_schema import SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE
from src.backend.backtest_terminal_v3_fence import TERMINAL_COMMIT_V3
from src.backend.backtest_fixed_run_context import verify_fixed_run_context
from src.backend.backtest_fixed_v3_preflight import (
    read_v3_preflight, running_v3_preflight, terminal_v3_preflight,
    terminal_v3_keeper_namespace_preflight,
)
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
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch


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
    token: FixedJournalPreflightToken | FixedV3JournalPreflightToken
    journal: BacktestMemoryJournal
    writer: ArteJournalWriter
    publisher: BacktestTypedJournalPublisher
    terminal_authority: FixedTerminalKeeperAuthority


@dataclass(frozen=True, slots=True)
class FixedV3JournalPreflightToken:
    run_id: str
    account_ids: tuple[str, ...]
    run_month: date
    configuration_hash: str
    market_plan_token: str
    query_sha256: str
    projection_certificate: str


def fixed_journal_operator_check(client: Any) -> dict[str, Any]:
    """Read-only inventory; V2-only readiness cannot authorize fixed launch."""
    missing = list(missing_fixed_backtest_v2_tables(client))
    required_v3 = (SQUEEZE_EPISODE, SQUEEZE_COMMIT_V3, TERMINAL_COMMIT_V3)
    names = ",".join(f"'{table.name}'" for table in required_v3)
    rows = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    installed = [row.get("name") for row in rows]
    if (len(installed) != len(set(installed))
            or any(set(row) != {"name"} or row["name"] not in
                   {table.name for table in required_v3} for row in rows)):
        raise RuntimeError("Fixed V3 table catalog is ambiguous")
    missing.extend(table.name for table in required_v3 if table.name not in installed)
    if missing:
        return {
            "id": "fixed_journal_authority",
            "label": "Normalized ClickHouse trading journal",
            "status": "blocked", "required": True,
            "summary": f"{len(missing)} required normalized arte journal tables are absent",
            "evidence": {"missing_tables": list(missing)},
        }
    # A table inventory is not a V3 grant or terminal-recovery certificate.
    return {
        "id": "fixed_journal_authority",
        "label": "Normalized ClickHouse trading journal",
        "status": "blocked", "required": True,
        "summary": "V3 terminal grants and full cold-recovery parity are not yet certified",
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
    context = verify_fixed_run_context(
        TypedInsertDispatch(keeper), read_client, terminal_client, run_id=run_id)
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != account_ids
            or context["configuration_hash"] != configuration_hash
            or context["market_plan_token"] != market_plan_token):
        raise RuntimeError("Fixed journal run context differs from pinned authority")
    month = date.fromisoformat(context["run_month"])
    if month.day != 1:
        raise RuntimeError("Fixed journal run month is invalid")
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
        return FixedJournalAssembly(token, journal, writer, publisher, authority)
    except BaseException:
        writer.close()
        journal.close()
        raise


def prepare_fixed_v3_journal_token(
    read_client: Any, writer_client: Any, terminal_client: Any,
    keeper: Any, *, run_id: str, account_ids: tuple[str, ...],
    configuration_hash: str, market_plan_token: str,
    expected_query_sha256: str,
    query_hash_certifier: Callable[[], str],
    projection_certifier: Callable[[], str],
) -> FixedV3JournalPreflightToken:
    """Read-only exact three-principal admission; never opens the launch gate."""
    if (len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or not account_ids or len(account_ids) != len(set(account_ids))
            or any(re.fullmatch(r"[0-9a-f]{64}", value or "") is None
                   for value in (configuration_hash, market_plan_token,
                                 expected_query_sha256))
            or not callable(query_hash_certifier)
            or not callable(projection_certifier)):
        raise ValueError("V3 journal lacks distinct pinned authorities")
    read_v3_preflight(read_client)
    running_v3_preflight(writer_client)
    terminal_v3_preflight(terminal_client)
    terminal_v3_keeper_namespace_preflight(keeper)
    context = verify_fixed_run_context(
        TypedInsertDispatch(keeper), read_client, terminal_client,
        run_id=run_id)
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != account_ids
            or context["configuration_hash"] != configuration_hash
            or context["market_plan_token"] != market_plan_token
            or load_typed_run_context(writer_client, run_id) != context):
        raise RuntimeError("V3 journal context differs across principals")
    if query_hash_certifier() != expected_query_sha256:
        raise RuntimeError("V3 squeeze query hash lacks source certification")
    certificate = projection_certifier()
    if (not isinstance(certificate, str)
            or re.fullmatch(r"[0-9a-f]{64}", certificate) is None):
        raise RuntimeError("V3 journal projector cannot certify emitted families")
    month = date.fromisoformat(context["run_month"])
    if month.day != 1:
        raise RuntimeError("V3 journal run month is invalid")
    return FixedV3JournalPreflightToken(
        run_id, account_ids, month, configuration_hash,
        market_plan_token, expected_query_sha256, certificate)


def assemble_fixed_v3_journal(
    read_client: Any, writer_client: Any, terminal_client: Any,
    keeper: Any, token: FixedV3JournalPreflightToken, *,
    attempt_id: str, expected_config: dict[str, Any],
    fixed_market_parent_plan: object, fixed_market_execution_plan: object,
    expected_market_start: datetime,
    writer_factory: Callable[..., ArteJournalWriter],
    batch_size: int = 512, queue_capacity: int = 8,
) -> FixedJournalAssembly:
    """Inactive bounded V3 lane; each principal stays on its own client."""
    if (not isinstance(token, FixedV3JournalPreflightToken)
            or len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or not 1 <= batch_size <= 4096 or not 1 <= queue_capacity <= 64
            or expected_market_start.tzinfo is None
            or fixed_market_parent_plan is None
            or fixed_market_execution_plan is None):
        raise ValueError("V3 bootstrap lacks bounded certified inputs")
    UUID(attempt_id)
    context = load_typed_run_context(read_client, token.run_id)
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != token.account_ids
            or context["configuration_hash"] != token.configuration_hash
            or context["market_plan_token"] != token.market_plan_token
            or load_typed_run_context(writer_client, token.run_id) != context
            or load_typed_run_context(terminal_client, token.run_id) != context):
        raise RuntimeError("V3 journal context changed before assembly")
    read_v3_preflight(read_client)
    running_v3_preflight(writer_client)
    terminal_v3_preflight(terminal_client)
    journal = BacktestMemoryJournal(run_id=token.run_id)
    try:
        writer = writer_factory(
            writer_client, run_id=token.run_id, capacity=queue_capacity,
            max_events_per_commit=batch_size, coalesce_batches=False,
            journal_profile="backtest_v3")
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=attempt_id, run_month=token.run_month,
            batch_size=batch_size, expected_config=expected_config,
            fixed_market_parent_plan=fixed_market_parent_plan,
            fixed_market_execution_plan=fixed_market_execution_plan,
            expected_market_start=expected_market_start,
            expected_market_plan_token=token.market_plan_token,
            expected_query_sha256=token.query_sha256)
        authority = FixedTerminalKeeperAuthority(
            keeper=keeper, client=terminal_client, run_id=token.run_id,
            account_ids=token.account_ids)
        return FixedJournalAssembly(token, journal, writer, publisher, authority)
    except BaseException:
        if "writer" in locals():
            writer.close()
        journal.close()
        raise

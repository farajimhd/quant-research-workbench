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


def _fixed_structural_lot_oms_image(owner,image,contexts):
    """Reuse the normalized production decoder, including observed frontiers."""
    from src.trading_runtime.fixed_structural_lot_manager_snapshot import require_cold_manager_image
    from src.trading_runtime.arte_oms_projection import (load_latest_committed_oms_groups,
        load_committed_oms_admission_page,load_committed_oms_decision_page,
        _approved_strategy_one_oms_intent,reconstruct_strategy_one_oms_lineage,RecoveredStrategyOneOmsLineage)
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
    from src.trading_runtime.arte_oms_actor_restore import (
        reconstruct_typed_oms_actor_image,attach_typed_oms_observations,verify_typed_oms_broker_open_orders)
    from src.trading_runtime.strategy_one_oms_observation_snapshot import load_unattested_oms_observation_snapshot
    from src.trading_runtime.strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
    from src.backend.backtest_market_data import market_day_boundary
    from datetime import timedelta
    source=owner.operation.source;require_cold_manager_image(image,source=source)
    prefix,_=owner._prefix()
    if (prefix.last_sequence,prefix.last_batch_id)!=(image.sequence,image.batch_id):
        raise ValueError('Selected OMS bootstrap has foreign committed cursor')
    roots=dict(v for v in image.financial_roots if v[0]!='portfolio')
    observed=load_unattested_oms_observation_snapshot(owner.client,run_id=source.run_id,checkpoint_sequence=image.sequence)
    broker=load_unattested_broker_match_snapshot(owner.client,run_id=source.run_id,checkpoint_sequence=image.sequence)
    if observed.root['content_hash']!=roots['root'] or broker.snapshot['content_hash']!=roots['snapshot']:
        raise ValueError('Selected OMS bootstrap financial root changed')
    groups=load_latest_committed_oms_groups(owner.client,prefix,
        strategy_identity=(source._strategy_id,source._revision),require_tactic=True,fixed_lot_contexts=contexts)
    history=load_complete_typed_protection_history(owner.client,prefix,fixed_lot_contexts=contexts)
    by_intent={v.base.intents[0]['intent_id']:v for v in contexts};lineages=[]
    from src.trading_runtime.selected_checkpoint_products import selected,source_bound_oms_lineages
    if selected(source):
        lineages=source_bound_oms_lineages(owner.client,prefix,source=source,contexts=contexts,
            allowed_accounts=frozenset(v[1] for v in image.financial_roots if v[0]=='portfolio'))
    else:
        for group in groups:
            ctx=by_intent.get(group.group['strategy_intent_id'])
            if ctx is None:raise ValueError('Selected OMS bootstrap lacks complete owned source lineage')
            request=ctx.verify_source()
            admission=load_committed_oms_admission_page(owner.client,prefix,(group,))[group.sequence]
            decision=load_committed_oms_decision_page(owner.client,prefix,(group,),{group.sequence:admission})[group.sequence]
            original=RecoveredIntent(ctx.record.sequence,ctx.record.account_id,ctx.record.record_id,ctx.base.batch_id,request.intent,ctx.base)
            approved,_=_approved_strategy_one_oms_intent(group,original,history,admission,decision)
            orders=reconstruct_strategy_one_oms_lineage(group,original,history,admission_reservation=admission,admission_decision=decision)
            lineages.append(RecoveredStrategyOneOmsLineage(group,original,orders,image.sequence,approved,admission))
    decoded=reconstruct_typed_oms_actor_image(tuple(lineages),history,run_id=source.run_id,
        strategy_id=source._strategy_id,strategy_revision=source._revision,through_sequence=image.sequence,
        cutoff_at=market_day_boundary(source.session_date,image.inherited.boundary_ms))
    decoded=attach_typed_oms_observations(decoded,observed,through_sequence=image.sequence)
    verify_typed_oms_broker_open_orders(decoded,broker)
    return decoded


async def restore_fixed_structural_lot_native_manager(owner, manager, keeper_session):
    """Selected cold bootstrap after ordinary Portfolio/broker/OMS recovery.

    Read persisted source and financial roots afresh. Caller captures, ceilings,
    live publication capabilities and requested-stop booleans grant no authority.
    The ordinary fixed bootstrap and restoration paths remain unchanged.
    """
    from dataclasses import replace
    from datetime import timedelta, timezone
    from zoneinfo import ZoneInfo
    from .backtest_fixed_structural_lot_management import (
        NativeFixedStructuralLotManagement,FixedStructuralLotManagerCheckpoint)
    from .backtest_strategy_one_management import StrategyOneManagementRunner
    from .backtest_strategy_one_financial import read_strategy_one_financial_views
    from src.trading_runtime.strategy_engine import StrategyAssignment
    from src.trading_runtime.fixed_structural_lot_manager_snapshot import load_cold_manager_image
    from src.trading_runtime.arte_portfolio_snapshot import (
        prepare_captured_portfolio_snapshot,_snapshot_rows,_state_hash,
        load_portfolio_snapshot,capture_portfolio_snapshot)
    if (type(owner) is not NativeFixedStructuralLotManagement
            or type(manager) is not StrategyOneManagementRunner or manager._fixed_lot_owner is not owner
            or owner.states or owner.entries or owner.financials
            or manager._submitted or manager._positions):
        raise ValueError('Selected bootstrap requires fresh actual manager owners')
    source=owner.operation.source;source.require_installed_admission()
    runtime=manager.runtime
    if (runtime.journal is not owner.publisher.journal or runtime.run_id!=source.run_id
            or (runtime.config.strategy_id,runtime.config.strategy_revision,runtime.config.anchor_date)!=
               (source._strategy_id,source._revision,source.session_date)):
        raise ValueError('Selected bootstrap has foreign native configuration/run/source')
    contexts=tuple(getattr(owner.client,'fixed_structural_lot_contexts',()))
    image=load_cold_manager_image(owner.client,keeper_session,source=source,fixed_lot_contexts=contexts)
    prefix,_=owner._prefix()
    if (prefix.last_sequence,prefix.last_batch_id)!=(image.sequence,image.batch_id):
        raise ValueError('Selected bootstrap committed cursor changed')
    instant=(datetime.combine(source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
        +timedelta(hours=4,milliseconds=image.inherited.boundary_ms)).astimezone(timezone.utc)
    portfolio_roots={v[1]:(v[2],v[3]) for v in image.financial_roots if v[0]=='portfolio'}
    if set(runtime.portfolio.states)!=set(portfolio_roots):
        raise ValueError('Selected bootstrap restored Portfolio account inventory differs')
    from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
    recovery=recover_portfolio_engine_state(owner.client,run_id=source.run_id,
        profiles=tuple(v.profile for _,v in sorted(runtime.portfolio.states.items())),
        state_revisions={account:image.sequence for account in portfolio_roots},cutoff_at=instant)
    for account,(expected_hash,snapshot_at) in portfolio_roots.items():
        original=load_portfolio_snapshot(owner.client,run_id=source.run_id,account_id=account,state_revision=image.sequence)
        if original is None or original['state_hash']!=expected_hash or datetime.fromisoformat(snapshot_at)!=instant:
            raise ValueError('Selected bootstrap pre-recovery committed Portfolio root differs')
        expected_capture=capture_portfolio_snapshot(run_id=source.run_id,state_revision=image.sequence,snapshot_at=instant,
            state=recovery.states[account],reservations=recovery.reservations.values(),
            allocations=recovery.allocations.values(),reconciliation=recovery.differences.values())
        expected=prepare_captured_portfolio_snapshot(expected_capture)
        transformed_hash=_state_hash(_snapshot_rows(expected.run_id,expected.account_id,expected.state_revision,expected.snapshot_month,expected.rows))
        captured=runtime.portfolio.capture_recovery_snapshot(account,state_revision=image.sequence,snapshot_at=instant)
        prepared=prepare_captured_portfolio_snapshot(captured)
        rows=_snapshot_rows(prepared.run_id,prepared.account_id,prepared.state_revision,prepared.snapshot_month,prepared.rows)
        if _state_hash(rows)!=transformed_hash:
            raise ValueError('Selected bootstrap restored Portfolio full state differs from committed root')
    from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
    from src.trading_runtime.strategy_one_oms_observation_snapshot import project_oms_observation_snapshot
    roots=dict(v for v in image.financial_roots if v[0]!='portfolio')
    actual_broker=project_broker_match_snapshot(run_id=source.run_id,session_date=source.session_date,
        checkpoint_sequence=image.sequence,boundary_ms=image.inherited.boundary_ms,state=runtime.broker.broker_match_snapshot_state())
    if actual_broker.snapshot['content_hash']!=roots['snapshot']:
        raise ValueError('Selected bootstrap complete actual broker checkpoint differs')
    if not runtime.order_manager.snapshots():
        from src.trading_runtime.arte_oms_actor_restore import install_typed_oms_actor_image
        install_typed_oms_actor_image(runtime.order_manager,_fixed_structural_lot_oms_image(owner,image,contexts))
    actual_oms=project_oms_observation_snapshot(run_id=source.run_id,session_date=source.session_date,
        checkpoint_sequence=image.sequence,boundary_ms=image.inherited.boundary_ms,groups=runtime.order_manager.capture_observed_broker_states())
    if actual_broker.snapshot['content_hash']!=roots['snapshot']:
        raise ValueError('Selected bootstrap complete actual broker checkpoint differs')
    if actual_oms.root['content_hash']!=roots['root']:
        raise ValueError('Selected bootstrap complete actual OMS observation checkpoint differs')
    assignments_method=getattr(runtime.strategy,'assignments',None)
    if not callable(assignments_method):
        raise ValueError('Selected bootstrap lacks actual native assignment owner')
    assignments=tuple(assignments_method())
    if (any(type(v) is not StrategyAssignment or (v.strategy_id,v.strategy_revision)!=(source._strategy_id,source._revision)
            or v.account_id not in portfolio_roots for v in assignments)
            or len({(v.account_id,v.assignment_id,v.ticker) for v in assignments})!=len(assignments)):
        raise ValueError('Selected bootstrap has duplicate or foreign actual assignments')
    active_keys=set(dict(image.inherited.positions));financials={}
    for ticker in sorted({v.ticker for v in assignments}):
        views=await read_strategy_one_financial_views(tuple(v for v in assignments if v.ticker==ticker),
            runtime.broker,runtime.order_manager)
        for view in views:
            key=(view.account_id,view.assignment_id,view.ticker)
            if key in active_keys:financials[key]=view
            elif view.position_quantity or view.pending_entry or view.pending_exit:
                raise ValueError('Selected bootstrap contains unexpected active financial ownership')
    if set(financials)!=active_keys:
        raise ValueError('Selected bootstrap omitted actual held assignment')
    from decimal import Decimal
    expected_positions={(key[0],key[2]):Decimal(str(view.position_quantity))
        for key,view in financials.items() if view.position_quantity}
    actual_positions={}
    for account in sorted(portfolio_roots):
        for row in await runtime.broker.positions(account):
            quantity=Decimal(str(row.position))
            if not quantity:continue
            key=(account,str(row.contractDesc).upper())
            if key in actual_positions:raise ValueError('Selected bootstrap repeated broker position')
            actual_positions[key]=quantity
    if actual_positions!=expected_positions:
        raise ValueError('Selected bootstrap complete actual broker inventory differs')
    from src.trading_runtime.arte_oms_projection import (
        load_latest_committed_oms_groups,load_committed_oms_admission_page,
        load_committed_oms_decision_page,_approved_strategy_one_oms_intent,
        reconstruct_strategy_one_oms_lineage,_duration_ms,_exact_decimal,freeze_oms_group)
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
    from src.trading_runtime.order_management import TERMINAL_MANAGEMENT_STATES
    groups=load_latest_committed_oms_groups(owner.client,prefix,allowed_accounts=frozenset(portfolio_roots),
        strategy_identity=(source._strategy_id,source._revision),require_tactic=True,fixed_lot_contexts=contexts)
    history=load_complete_typed_protection_history(owner.client,prefix,fixed_lot_contexts=contexts)
    context_by_intent={v.base.intents[0]['intent_id']:v for v in contexts}
    from src.trading_runtime.selected_checkpoint_products import selected,source_bound_oms_lineages
    selected_lineages={}
    if selected(source):
        selected_lineages={v.state.group['group_id']:v for v in source_bound_oms_lineages(
            owner.client,prefix,source=source,contexts=contexts,allowed_accounts=frozenset(portfolio_roots))}
    live=tuple(freeze_oms_group(group) for group in runtime.order_manager._groups.values())
    live_by_id={v.group_id:v for v in live}
    if len(live_by_id)!=len(live):raise ValueError('Selected bootstrap repeated actual OMS group')
    expected_ids=set()
    for stored in groups:
        lineage=selected_lineages.get(stored.group['group_id'])
        if selected(source) and lineage is None:
            raise ValueError('Selected bootstrap has missing verified OMS lineage')
        ctx=context_by_intent.get(lineage.source_entry_intent_id if lineage is not None
            and lineage.source_entry_intent_id is not None else stored.group['strategy_intent_id'])
        if ctx is None:raise ValueError('Selected bootstrap group lacks actual source companion')
        request=ctx.verify_source();key=(request.entry.proposal.account_id,request.entry.proposal.assignment_id,request.entry.proposal.ticker)
        if key not in active_keys:continue
        if lineage is not None:
            approved=lineage.approved_intent;orders=lineage.orders
        else:
            admission=load_committed_oms_admission_page(owner.client,prefix,(stored,))[stored.sequence]
            decision=load_committed_oms_decision_page(owner.client,prefix,(stored,),{stored.sequence:admission})[stored.sequence]
            original=RecoveredIntent(ctx.record.sequence,ctx.record.account_id,ctx.record.record_id,
                ctx.base.batch_id,request.intent,ctx.base)
            approved,_=_approved_strategy_one_oms_intent(stored,original,history,admission,decision)
            orders=reconstruct_strategy_one_oms_lineage(stored,original,history,
                admission_reservation=admission,admission_decision=decision)
        actual=live_by_id.get(stored.group['group_id'])
        if (actual is None or actual.account_id!=key[0] or actual.intent.metadata.get('assignment_id')!=key[1]
                or actual.intent.ticker!=key[2] or actual.intent!=approved or tuple(actual.orders)!=tuple(orders)
                or actual.broker_order_request_indexes!={v['broker_order_id']:v['request_index'] for v in stored.broker_bindings if lineage is None or v['request_index'] is not None}
                or actual.filled_by_broker_order!={v['broker_order_id']:float(v['filled_quantity']) for v in stored.broker_bindings if lineage is None or v['has_filled_quantity']}
                or actual.terminal_broker_order_ids!={v['broker_order_id'] for v in stored.broker_bindings if v['terminal']}):
            raise ValueError('Selected bootstrap actual OMS/source/order/ACK inventory differs')
        from src.trading_runtime.arte_journal_reader import _journal_instant
        if actual.state.value!=stored.group['state']:
            raise ValueError('Selected bootstrap actual OMS management state differs')
        for field in ('created_at','updated_at','submitted_at','last_reprice_at','failed_reprice_at'):
            expected=stored.group[field]
            if getattr(actual,field)!=(_journal_instant(expected) if expected is not None else None):
                raise ValueError('Selected bootstrap actual OMS causal timing differs')
        for field in ('decision_to_submit_ms','internal_reaction_ms'):
            if _duration_ms(getattr(actual,field))!=stored.group[field]:
                raise ValueError('Selected bootstrap actual OMS timing telemetry differs')
        for field in ('filled_quantity','remaining_quantity','high_water_price','low_water_price',
                'protection_required_quantity','protection_coverage_quantity','current_limit_price'):
            value=getattr(actual,field)
            if (None if value is None else _exact_decimal(value))!=stored.group[field]:
                raise ValueError('Selected bootstrap actual OMS financial/protection scalar differs')
        if (actual.rejection_reason!=stored.group['rejection_reason']
                or actual.reprice_count!=stored.group['reprice_count']
                or int(actual.protection_delegated)!=stored.group['protection_delegated']
                or actual.broker_order_roles!={v['broker_order_id']:v['role'] for v in stored.broker_bindings if lineage is None or v['has_role']}
                or actual.broker_order_slices!={v['broker_order_id']:v['slice_id'] for v in stored.broker_bindings if lineage is None or v['has_slice']}):
            raise ValueError('Selected bootstrap actual OMS flags/binding roles differ')
        deferred=actual.deferred_reprice
        if ((tuple(_exact_decimal(v) for v in deferred) if deferred is not None else (None,None))!=
                (stored.group['deferred_reprice_from'],stored.group['deferred_reprice_to'])):
            raise ValueError('Selected bootstrap actual OMS pending reprice differs')
        expected_ids.add(actual.group_id)
    if any(v.group_id not in expected_ids and v.state not in TERMINAL_MANAGEMENT_STATES for v in live):
        raise ValueError('Selected bootstrap unexpected active or acquiring OMS group')
    # Restore validates fresh source/normalized OMS roster and exact quantities
    # independently; no scalar ceiling or supplied ACK enters this checkpoint.
    checkpoint=FixedStructuralLotManagerCheckpoint(image.inherited,image.selected_positions,tuple(sorted(financials.items())))
    owner.restore(manager,checkpoint)
    return image

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_squeeze_episode_schema import (
    RECONCILIATION_DIFFERENCE, RESERVATION_REASON,
    SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE, PROTECTION_CHANGE_TABLES,
)
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.backend.backtest_terminal_v3_fence import TERMINAL_COMMIT_V3
from src.backend.backtest_fixed_run_context import (
    _validate_local_context, publish_fixed_run_context,
    verify_fixed_run_context,
)
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
    V4_COMMIT_TABLES, fixed_backtest_v2_contracts, missing_fixed_backtest_v2_tables,
    fixed_backtest_v2_preflight, storage_preflight,
)
from src.trading_runtime.arte_journal_commit_v4 import MAX_V4_COMMIT_EVENTS
from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy
from src.trading_runtime.arte_broker_acknowledgement_v4 import ACKNOWLEDGEMENT
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, _V4PreflightSeal, _v4_preflight, load_typed_run_context,
    v4_storage_contracts,
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
    token: FixedJournalPreflightToken | FixedV3JournalPreflightToken | FixedV4JournalPreflightToken
    journal: BacktestMemoryJournal
    writer: ArteJournalWriter
    publisher: BacktestTypedJournalPublisher
    terminal_authority: FixedTerminalKeeperAuthority | None


@dataclass(frozen=True, slots=True)
class FixedV3JournalPreflightToken:
    run_id: str
    account_ids: tuple[str, ...]
    run_month: date
    configuration_hash: str
    market_plan_token: str
    query_sha256: str
    projection_certificate: str


@dataclass(frozen=True, slots=True)
class FixedV4JournalPreflightToken:
    run_id: str
    account_ids: tuple[str, ...]
    run_month: date
    configuration_hash: str
    market_plan_token: str
    projection_certificate: str


_V4_CONTEXT_SECRET = object()


class _V4PublishedContextSeal:
    """One-launch receipt for the exact three clients already cold-verified."""

    __slots__ = ("context", "clients", "used")

    def __init__(self, context: dict[str, Any], clients: tuple[Any, Any, Any],
                 secret: object) -> None:
        if secret is not _V4_CONTEXT_SECRET:
            raise RuntimeError("V4 published context seal requires its publication path")
        self.context = dict(context)
        self.clients = clients
        self.used = False

    def consume(self, clients: tuple[Any, Any, Any], run_id: str) -> dict[str, Any]:
        if self.used or any(actual is not expected
                            for actual, expected in zip(clients, self.clients)):
            raise RuntimeError("V4 published context seal belongs to different clients")
        if self.context.get("run_id") != run_id:
            raise RuntimeError("V4 published context seal belongs to a different run")
        self.used = True
        return self.context


def fixed_journal_operator_check(client: Any) -> dict[str, Any]:
    """Read-only inventory; V2-only readiness cannot authorize fixed launch."""
    missing = list(missing_fixed_backtest_v2_tables(client))
    required_v3 = (SQUEEZE_EPISODE, RESERVATION_REASON,
                   RECONCILIATION_DIFFERENCE,
                   *TRADE_PROPOSAL_TABLES,
                   *PROTECTION_CHANGE_TABLES, SQUEEZE_COMMIT_V3, TERMINAL_COMMIT_V3)
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


def assemble_resumed_fixed_v4_journal(
    read_client: Any, writer_client: Any, terminal_client: Any,
    token: FixedV4JournalPreflightToken, *, attempt_id: str,
    expected_config: dict[str, Any], fixed_market_parent_plan: object,
    fixed_market_execution_plan: object, expected_market_start: datetime,
    code_hash: str, recovery_evidence: object,
    writer_factory: Callable[..., ArteJournalWriter],
    batch_size: int = 1024, queue_capacity: int = 8,
    configuration_revision=None, fixed_lot_resume=None,
) -> tuple[FixedJournalAssembly, Any]:
    """Cold-seed a V4 lane from Keeper plus the exact committed market cursor.

    No app controller calls this until all financial and causal actors have a
    complete normalized restore. It never reads a run-local file or SQLite.
    """
    from src.backend.backtest_fixed_running_anchor import (
        cold_verify_v4_resume_anchor,
    )
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.backend.backtest_market_data import CertifiedMarketDayPlan
    from src.backend.backtest_v4_running_recovery import (
        verify_v4_recovery_at_anchor,
    )

    lease = getattr(writer_client, "backtest_v4_lease", None)
    dispatch = getattr(writer_client, "typed_insert_dispatch", None)
    if (not isinstance(token, FixedV4JournalPreflightToken)
            or not isinstance(lease, BacktestV4KeeperLease)
            or lease.run_id != token.run_id
            or not isinstance(dispatch, TypedInsertDispatch)
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or not isinstance(fixed_market_parent_plan, CertifiedMarketDayPlan)
            or fixed_market_parent_plan.token != token.market_plan_token
            or fixed_market_execution_plan is None
            or len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or not 1 <= batch_size <= MAX_V4_COMMIT_EVENTS
            or not 1 <= queue_capacity <= 64
            or expected_market_start.tzinfo is None):
        raise ValueError("V4 cold journal lacks exact owner, plan, or bounds")
    from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
    drawdown_authority = bind_run_drawdown_authority(run_id=token.run_id,
        expected_config=expected_config, configuration_hash=token.configuration_hash,
        configuration_revision=configuration_revision)
    lease.assert_current()
    UUID(attempt_id)
    anchor = cold_verify_v4_resume_anchor(
        read_client, dispatch=dispatch, lease=lease, run_id=token.run_id,
        plan=fixed_market_parent_plan,
        configuration_hash=token.configuration_hash,
        account_ids=token.account_ids, code_hash=code_hash,
        **({"fixed_lot_resume":fixed_lot_resume} if fixed_lot_resume is not None else {}))
    verify_v4_recovery_at_anchor(recovery_evidence, anchor)
    context = load_typed_run_context(writer_client, token.run_id)
    if (context != load_typed_run_context(terminal_client, token.run_id)
            or context.get("mode") != "backtest"
            or tuple(context.get("account_ids") or ()) != token.account_ids
            or context.get("configuration_hash") != token.configuration_hash
            or context.get("market_plan_token") != token.market_plan_token
            or context.get("code_hash") != code_hash):
        raise RuntimeError("V4 resumed journal context differs across principals")
    if drawdown_authority is not None:
        drawdown_authority.verify(run_id=token.run_id, expected_config=context,
                                  configuration_hash=token.configuration_hash)
    if writer_factory is not ArteJournalWriter:
        _v4_preflight(writer_client)
    assembly = _assemble_v4_writer_lane(
        writer_client, token, attempt_id=attempt_id,
        expected_config=expected_config, drawdown_authority=drawdown_authority,
        fixed_market_parent_plan=fixed_market_parent_plan,
        fixed_market_execution_plan=fixed_market_execution_plan,
        expected_market_start=expected_market_start,
        writer_factory=writer_factory, batch_size=batch_size,
        queue_capacity=queue_capacity,
        initial_sequence=anchor.journal_sequence,
        prior_batch_id=anchor.batch_id,
        source_cursor=anchor.source_cursor)
    try:
        if fixed_lot_resume is not None:
            from .backtest_fixed_structural_lot_resume import require_fixed_structural_lot_resume
            binding=require_fixed_structural_lot_resume(fixed_lot_resume,token.run_id)
            assembly.publisher.restore_fixed_structural_lot_source(binding.source,
                prefix=recovery_evidence.prefix,contexts=binding.contexts)
        assembly.journal.restore_verified_campaign_ownership(
            recovery_evidence.campaign)
        assembly.journal.restore_verified_portfolio_admissions(
            recovery_evidence.oms)
        from src.trading_runtime.arte_journal_reader import (
            load_complete_typed_protection_history,
        )
        protection_history = load_complete_typed_protection_history(
            read_client, recovery_evidence.prefix,
            **({"fixed_lot_contexts":fixed_lot_resume.contexts} if fixed_lot_resume is not None else {}))
        assembly.journal.restore_committed_records(protection_history.records)
        assembly.publisher.restore_verified_oms_sources(
            recovery_evidence.oms, protection_history,
            **({"fixed_lot_resume":fixed_lot_resume} if fixed_lot_resume is not None else {}))
    except BaseException:
        assembly.writer.close()
        assembly.journal.close()
        raise
    return assembly, anchor


def _assemble_v4_writer_lane(
    writer_client: Any, token: FixedV4JournalPreflightToken, *,
    attempt_id: str, expected_config: dict[str, Any],
    fixed_market_parent_plan: object, fixed_market_execution_plan: object,
    expected_market_start: datetime, writer_factory: Callable[..., ArteJournalWriter],
    batch_size: int, queue_capacity: int,
    v4_preflight_seal: _V4PreflightSeal | None = None,
    initial_sequence: int = 0,
    prior_batch_id: str = "00000000-0000-0000-0000-000000000000",
    source_cursor: str = "start",
    drawdown_authority=None,
) -> FixedJournalAssembly:
    from src.trading_runtime.drawdown_measure_authority import projection_drawdown_policy
    projection_drawdown_policy(drawdown_authority, run_id=token.run_id,
                              expected_config=expected_config)
    if drawdown_authority is not None:
        drawdown_authority.verify(run_id=token.run_id, expected_config=expected_config,
                                  configuration_hash=token.configuration_hash)
    journal_type, publisher_type = BacktestMemoryJournal, BacktestTypedJournalPublisher
    journal = journal_type(
        run_id=token.run_id, initial_sequence=initial_sequence)
    try:
        writer_kwargs = ({"v4_preflight_seal": v4_preflight_seal}
                         if v4_preflight_seal is not None else {})
        writer = writer_factory(
            writer_client, run_id=token.run_id, capacity=queue_capacity,
            max_events_per_commit=batch_size, coalesce_batches=False,
            journal_profile="backtest_v4", **writer_kwargs)
        publisher = publisher_type(
            journal, writer, attempt_id=attempt_id, run_month=token.run_month,
            batch_size=batch_size, expected_config=expected_config,
            drawdown_authority=drawdown_authority,
            fixed_market_parent_plan=fixed_market_parent_plan,
            fixed_market_execution_plan=fixed_market_execution_plan,
            expected_market_start=expected_market_start,
            initial_sequence=initial_sequence, prior_batch_id=prior_batch_id,
            source_cursor=source_cursor)
        return FixedJournalAssembly(token, journal, writer, publisher, None)
    except BaseException:
        if "writer" in locals():
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
    configuration_revision=None,
) -> FixedJournalAssembly:
    """Inactive bounded V3 lane; each principal stays on its own client."""
    if (not isinstance(token, FixedV3JournalPreflightToken)
            or len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or not 1 <= batch_size <= 4096 or not 1 <= queue_capacity <= 64
            or expected_market_start.tzinfo is None
            or fixed_market_parent_plan is None
            or fixed_market_execution_plan is None):
        raise ValueError("V3 bootstrap lacks bounded certified inputs")
    from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
    drawdown_authority = bind_run_drawdown_authority(run_id=token.run_id,
        expected_config=expected_config, configuration_hash=token.configuration_hash,
        configuration_revision=configuration_revision)
    UUID(attempt_id)
    context = load_typed_run_context(read_client, token.run_id)
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != token.account_ids
            or context["configuration_hash"] != token.configuration_hash
            or context["market_plan_token"] != token.market_plan_token
            or load_typed_run_context(writer_client, token.run_id) != context
            or load_typed_run_context(terminal_client, token.run_id) != context):
        raise RuntimeError("V3 journal context changed before assembly")
    if drawdown_authority is not None:
        drawdown_authority.verify(run_id=token.run_id, expected_config=context,
                                  configuration_hash=token.configuration_hash)
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
            drawdown_authority=drawdown_authority,
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


def prepare_fixed_v4_journal_token(
    read_client: Any, writer_client: Any, terminal_client: Any, *,
    run_id: str, account_ids: tuple[str, ...], configuration_hash: str,
    market_plan_token: str, projection_certifier: Callable[[], str],
) -> FixedV4JournalPreflightToken:
    """Certify a pre-published Strategy 1 context without inserting facts."""
    dispatch = getattr(writer_client, "typed_insert_dispatch", None)
    if (len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or not account_ids or len(set(account_ids)) != len(account_ids)
            or any(not isinstance(value, str) or not value for value in account_ids)
            or any(re.fullmatch(r"[0-9a-f]{64}", value or "") is None
                   for value in (configuration_hash, market_plan_token))
            or not callable(projection_certifier)):
        raise ValueError("V4 journal lacks distinct strict pinned authorities")
    for client in (read_client, terminal_client):
        storage_preflight(client, tables=v4_storage_contracts())
    _v4_preflight(writer_client)
    context = verify_fixed_run_context(
        dispatch, read_client, terminal_client, run_id=run_id)
    if load_typed_run_context(writer_client, run_id) != context:
        raise RuntimeError("V4 journal context differs across principals")
    certificate = projection_certifier()
    return _verified_v4_token(
        context, run_id=run_id, account_ids=account_ids,
        configuration_hash=configuration_hash,
        market_plan_token=market_plan_token, certificate=certificate)


def _verified_v4_token(
    context: dict[str, Any], *, run_id: str, account_ids: tuple[str, ...],
    configuration_hash: str, market_plan_token: str, certificate: str,
) -> FixedV4JournalPreflightToken:
    """Only seal a context already cold-verified across independent readers."""
    if (not isinstance(context, dict) or context.get("run_id") != run_id
            or context.get("mode") != "backtest"
            or tuple(context.get("account_ids") or ()) != account_ids
            or context.get("configuration_hash") != configuration_hash
            or context.get("market_plan_token") != market_plan_token):
        raise RuntimeError("V4 journal context differs across principals")
    if (not isinstance(certificate, str)
            or re.fullmatch(r"[0-9a-f]{64}", certificate) is None):
        raise RuntimeError("V4 journal projector cannot certify emitted families")
    month = date.fromisoformat(context["run_month"])
    if month.day != 1:
        raise RuntimeError("V4 journal run month is invalid")
    return FixedV4JournalPreflightToken(
        run_id, account_ids, month, configuration_hash,
        market_plan_token, certificate)


def assemble_fixed_v4_journal(
    read_client: Any, writer_client: Any, terminal_client: Any,
    token: FixedV4JournalPreflightToken, *, attempt_id: str,
    expected_config: dict[str, Any], fixed_market_parent_plan: object,
    fixed_market_execution_plan: object, expected_market_start: datetime,
    writer_factory: Callable[..., ArteJournalWriter],
    batch_size: int = 1024, queue_capacity: int = 8,
    v4_preflight_seal: _V4PreflightSeal | None = None,
    published_context_seal: _V4PublishedContextSeal | None = None,
    drawdown_authority=None,
) -> FixedJournalAssembly:
    """Build one bounded memory-to-Keeper writer lane; never open the gate."""
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease

    owner = getattr(writer_client, "backtest_v4_lease", None)
    if (not isinstance(token, FixedV4JournalPreflightToken)
            or len({id(read_client), id(writer_client), id(terminal_client)}) != 3
            or not 1 <= batch_size <= MAX_V4_COMMIT_EVENTS or not 1 <= queue_capacity <= 64
            or expected_market_start.tzinfo is None
            or fixed_market_parent_plan is None
            or fixed_market_execution_plan is None
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(writer_client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)
            or writer_factory is ArteJournalWriter and (
                not isinstance(owner, BacktestV4KeeperLease)
                or owner.run_id != token.run_id)):
        raise ValueError("V4 bootstrap lacks bounded strict certified inputs")
    if owner is not None:
        owner.assert_current()
    UUID(attempt_id)
    if published_context_seal is None:
        context = load_typed_run_context(read_client, token.run_id)
        same_context = (load_typed_run_context(writer_client, token.run_id) == context
                        and load_typed_run_context(terminal_client, token.run_id) == context)
    else:
        context = published_context_seal.consume(
            (read_client, writer_client, terminal_client), token.run_id)
        same_context = True
    if (context["mode"] != "backtest"
            or tuple(context["account_ids"]) != token.account_ids
            or context["configuration_hash"] != token.configuration_hash
            or context["market_plan_token"] != token.market_plan_token
            or not same_context):
        raise RuntimeError("V4 journal context changed before assembly")
    # The production writer constructor performs this exact V4 storage and
    # grant audit before starting its thread. Keep the explicit audit for
    # injected factories, which may not enforce that constructor contract.
    if drawdown_authority is not None:
        drawdown_authority.verify(run_id=token.run_id, expected_config=context,
                                  configuration_hash=token.configuration_hash)
    if writer_factory is not ArteJournalWriter:
        if v4_preflight_seal is not None:
            raise ValueError("Injected V4 writer cannot consume a production preflight")
        _v4_preflight(writer_client)
    return _assemble_v4_writer_lane(
        writer_client, token, attempt_id=attempt_id,
        expected_config=expected_config, drawdown_authority=drawdown_authority,
        fixed_market_parent_plan=fixed_market_parent_plan,
        fixed_market_execution_plan=fixed_market_execution_plan,
        expected_market_start=expected_market_start,
        writer_factory=writer_factory, batch_size=batch_size,
        queue_capacity=queue_capacity, v4_preflight_seal=v4_preflight_seal)


def publish_and_assemble_fixed_v4_journal(
    context_client: Any, read_client: Any, writer_client: Any,
    terminal_client: Any, *, run: dict[str, Any], config: dict[str, Any],
    account_ids: tuple[str, ...], attempt_id: str,
    expected_config: dict[str, Any], fixed_market_parent_plan: object,
    fixed_market_execution_plan: object,
    expected_market_start: datetime,
    projection_certifier: Callable[[], str],
    writer_factory: Callable[..., ArteJournalWriter],
    batch_size: int = 1024, queue_capacity: int = 8,
    configuration_revision=None,
) -> FixedJournalAssembly:
    """Publish a new fenced context and assemble V4 with no local persistence.

    The caller owns four distinct clients and their shared Keeper session.
    Do not retry a failed or ambiguous publication with the same run ID; cold
    verification must resolve its Keeper operation before any continuation.
    """
    from src.backend.backtest_fixed_market_authority import _validate_plans

    dispatch = getattr(context_client, "typed_insert_dispatch", None)
    runner_dispatch = getattr(writer_client, "typed_insert_dispatch", None)
    if (len({id(context_client), id(read_client), id(writer_client),
             id(terminal_client)}) != 4
            or getattr(context_client, "typed_insert_strict", False) is not True
            or getattr(writer_client, "typed_insert_strict", False) is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or not isinstance(runner_dispatch, TypedInsertDispatch)
            or dispatch.keeper is not runner_dispatch.keeper
            or not callable(projection_certifier)
            or not callable(writer_factory)):
        raise ValueError("V4 launch lacks distinct strict shared-Keeper authorities")
    run_id = _validate_local_context(run, config, account_ids)
    _validate_plans(fixed_market_parent_plan, fixed_market_execution_plan)
    # expected_config is the flat RunConfig projection emitted into the typed
    # journal, not the full Strategy Studio payload. The two authorities were
    # accidentally conflated here, blocking every real V4 launch.
    if (not is_numbered_fixed_strategy(expected_config.get("strategy_id"), expected_config.get("strategy_revision"))
            or run["evaluation_interval_ms"] != 100
            or run["market_plan_token"] != fixed_market_parent_plan.token
            or expected_market_start.tzinfo is None):
        raise ValueError("V4 launch requires pinned Strategy 1 at 100 ms")
    from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
    drawdown_authority = bind_run_drawdown_authority(run_id=run_id,
        expected_config=expected_config, configuration_hash=run["configuration_hash"],
        configuration_revision=configuration_revision)
    if drawdown_authority is not None:
        drawdown_authority.verify(run_id=run_id, expected_config=config,
                                  configuration_hash=run['configuration_hash'])
    UUID(attempt_id)
    if not 1 <= batch_size <= MAX_V4_COMMIT_EVENTS or not 1 <= queue_capacity <= 64:
        raise ValueError("V4 launch journal bounds are invalid")
    # Every reversible check runs before the first Keeper gate or ClickHouse
    # INSERT. Once publication starts, failures remain cold-recovery work.
    fixed_backtest_v2_preflight(context_client)
    _v4_cold_reader_preflight(read_client)
    _v4_cold_reader_preflight(terminal_client)
    writer_preflight_seal = _v4_preflight(writer_client)
    certificate = projection_certifier()
    if (not isinstance(certificate, str)
            or re.fullmatch(r"[0-9a-f]{64}", certificate) is None):
        raise RuntimeError("V4 launch projector cannot certify emitted families")
    published_context = publish_fixed_run_context(
        context_client, read_client, terminal_client, dispatch,
        run=run, config=config, account_ids=account_ids)
    # Publication already cold-verified both independent readers behind a
    # Keeper barrier. Rechecking that same context immediately adds expensive
    # catalog scans and four ClickHouse reads per reader, but no new fact.
    # The writer still performs its own context read before assembly.
    if load_typed_run_context(writer_client, run_id) != published_context:
        raise RuntimeError("V4 journal writer observes a different published context")
    token = _verified_v4_token(
        published_context, run_id=run_id, account_ids=account_ids,
        configuration_hash=run["configuration_hash"],
        market_plan_token=run["market_plan_token"], certificate=certificate)
    return assemble_fixed_v4_journal(
        read_client, writer_client, terminal_client, token,
        attempt_id=attempt_id, expected_config=expected_config, drawdown_authority=drawdown_authority,
        fixed_market_parent_plan=fixed_market_parent_plan,
        fixed_market_execution_plan=fixed_market_execution_plan,
        expected_market_start=expected_market_start,
        writer_factory=writer_factory, batch_size=batch_size,
        queue_capacity=queue_capacity,
        v4_preflight_seal=(writer_preflight_seal
                           if writer_factory is ArteJournalWriter else None),
        published_context_seal=_V4PublishedContextSeal(
            published_context, (read_client, writer_client, terminal_client),
            _V4_CONTEXT_SECRET))


def _v4_cold_reader_preflight(client: Any) -> None:
    """Use a server-enforced read-only V4 connection, never a V3 catalog.

    The writable V4 client audits the exact schema/grants once above. This
    check prevents the cold-audit connections from issuing INSERTs even when
    their underlying V4 principal also has journal INSERT grants.
    """
    if client.execute("SELECT getSetting('readonly')").strip() != "1":
        raise RuntimeError("V4 cold reader must have ClickHouse readonly=1")
    profiles = (('automatic_ladder_profile', 'backtest_v4_ladder_runner'),
                ('entry_spread_risk_profile', 'backtest_v4_entry_cost_runner'))
    selected = []
    for attribute, principal in profiles:
        enabled = getattr(client, attribute, False)
        if type(enabled) is not bool:
            raise RuntimeError('V4 cold reader profile selection must be explicit')
        if enabled:
            selected.append(principal)
    if len(selected) > 1:
        raise RuntimeError('V4 cold reader has conflicting profiles')
    expected_principal = selected[0] if selected else 'backtest_v4_runner'
    geometry_policy = getattr(client, 'ladder_geometry_policy', None)
    from src.trading_runtime.arte_journal_writer import _validate_ladder_geometry_profile
    _validate_ladder_geometry_profile(getattr(client, 'automatic_ladder_profile', False), geometry_policy)
    if geometry_policy is not None:
        expected_principal = 'backtest_v4_waiting_ladder_runner'
    risk_policy=getattr(client,'confirmed_original_risk_policy',None)
    from src.trading_runtime.original_risk_diagnostic_profile import validate_original_risk_profile
    validate_original_risk_profile(getattr(client,'automatic_ladder_profile',False),
        getattr(client,'entry_spread_risk_profile',False),risk_policy)
    if risk_policy is not None:
        expected_principal='backtest_v4_original_risk_runner'
    lot_profile=getattr(client,'fixed_structural_lot_profile',None)
    if lot_profile is not None:
        from src.trading_runtime.arte_journal_writer import _validate_fixed_structural_lot_profile
        _validate_fixed_structural_lot_profile(lot_profile,
            automatic_ladder=getattr(client,'automatic_ladder_profile',False),
            entry_spread_risk=getattr(client,'entry_spread_risk_profile',False),
            ladder_geometry_policy=geometry_policy,risk_policy=risk_policy)
        expected_principal='backtest_v4_fixed_structural_lot_runner'
    if client.execute("SELECT currentUser()").strip() != expected_principal:
        raise RuntimeError("V4 cold reader has unexpected principal")

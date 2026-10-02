"""Bounded asynchronous publication of a typed fixed-Backtest journal prefix.

This adapter is intentionally not wired into launch until terminal recovery
and the fixed-market execution path are validated end to end.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import (
    NIL_BATCH_ID, project_pending_backtest_prefix, project_pending_backtest_v3_prefix,
    project_pending_backtest_v4_prefix,
)
from src.trading_runtime.arte_followthrough_failure_v4 import V4FollowThroughFailureBatch
from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch
from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, TypedJournalBatch, V3SqueezeBatch,
    V4StrategyOneEntryBatch, V4BrokerAcknowledgementBatch, V4OrderCancelBatch,
    V4OrderRepriceBatch,
    V4ProtectionChangeBatch, _coalesce_unpublished, typed_row,
)
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)
from src.trading_runtime.arte_risk_action_v4 import V4RiskActionBatch
from src.trading_runtime.arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from src.trading_runtime.arte_reservation_reason_v4 import V4ReservationReasonBatch
from src.trading_runtime.arte_journal_compound_v4 import (
    V4CompoundBatch, coalesce_v4_units,
)
from src.trading_runtime.arte_oms_tactic_projection import V4OmsTacticBatch


def _coalesce_v4_units(units: tuple, *, max_events: int = 512) -> tuple:
    """Commit a bounded causal prefix, including same-batch intent consumers.

    The V4 family seal verifies the exact earlier intent revision in the same
    batch (or an already committed batch). There is no external broker in a
    Backtest, so the intent and its simulated OMS action share one durability
    boundary without weakening live order admission.
    """
    if len(units) > 1:
        from src.trading_runtime.arte_journal_commit_v4 import MAX_V4_COMMIT_EVENTS
        if (type(max_events) is not int or not 2 <= max_events <= MAX_V4_COMMIT_EVENTS
                or sum(len((unit if type(unit) is TypedJournalBatch else unit.base).events)
                       for unit in units) > max_events):
            raise ValueError('V4 publication prefix exceeds its event bound')
    if len(units) == 1:
        return units
    if not any(type(unit) in (V4ProfitGivebackBatch, V4ConfirmedAhFailureBatch, V4LiquidityFadeFailureBatch) for unit in units):
        return (coalesce_v4_units(units, max_events=max_events),)
    # These exits load their original entry from a sealed predecessor. They
    # must begin a new commit even when the entry and exit fit in one chunk.
    groups = []
    pending = []
    prior_intents = {}
    def flush():
        # Rekey only exact references to intents in earlier groups. This map
        # is bounded by this projected prefix and performs no database reads.
        adapted = []
        for unit in pending:
            base = unit if type(unit) is TypedJournalBatch else unit.base
            uses = []
            changed = False
            for row in base.intent_uses:
                previous = prior_intents.get(str(UUID(str(row['intent_record_id']))))
                if previous is not None:
                    original, committed = previous
                    if row['intent_content_hash'] != original:
                        raise ValueError('Cross-group intent use differs from its original source')
                    row = {**{key: value for key, value in row.items() if key != 'content_hash'},
                           'intent_content_hash': committed}
                    changed = True
                uses.append(row)
            if changed:
                base = replace(base, intent_uses=tuple(uses))
                unit = base if type(unit) is TypedJournalBatch else replace(unit, base=base)
            adapted.append(unit)
        group = (coalesce_v4_units(tuple(adapted), max_events=max_events)
                 if len(adapted) > 1 else adapted[0])
        final = group if type(group) is TypedJournalBatch else group.base
        final_hashes = {str(UUID(str(row['record_id']))): typed_row(
            'trading_strategy_intent_v1', {k: v for k, v in row.items() if k != 'content_hash'}
        )['content_hash'] for row in final.intents}
        for unit in pending:
            base = unit if type(unit) is TypedJournalBatch else unit.base
            for row in base.intents:
                identity = str(UUID(str(row['record_id'])))
                original = typed_row('trading_strategy_intent_v1', {
                    k: v for k, v in row.items() if k != 'content_hash'})['content_hash']
                prior_intents[identity] = (original, final_hashes[identity])
        groups.append(group)
    for unit in units:
        if type(unit) in (V4ProfitGivebackBatch, V4ConfirmedAhFailureBatch, V4LiquidityFadeFailureBatch) and pending:
            flush()
            pending = []
        pending.append(unit)
    if pending:
        flush()
    return tuple(groups)


def _committed_intent_source(batch: TypedJournalBatch,
                             record_id: str) -> TypedJournalBatch:
    """Pin one source revision to the actual committed compound batch ID."""
    events = tuple(row for row in batch.events if row["record_id"] == record_id)
    intents = tuple(row for row in batch.intents if row["record_id"] == record_id)
    slices = tuple(row for row in batch.intent_slices
                   if row["parent_record_id"] == record_id)
    if (len(events) != 1 or len(intents) != 1
            or events[0]["category"] != "strategy"
            or events[0]["entity_type"] != "strategy_intent"
            or any(row["batch_id"] != batch.batch_id
                   for row in (*events, *intents, *slices))):
        raise RuntimeError("Committed V4 intent source is not one normalized revision")
    sequence = int(events[0]["sequence"])
    return TypedJournalBatch(
        batch.run_id, batch.run_month, batch.attempt_id, batch.batch_id,
        batch.prior_batch_id, sequence, sequence, batch.source_cursor,
        batch.status, events, intents=intents, intent_slices=slices)


@dataclass(frozen=True, slots=True)
class TypedBacktestReceipt:
    last_sequence: int
    last_batch_id: str
    source_cursor: str


class BacktestTypedJournalPublisher:
    """One exclusive writer lane; engine enqueues, a task finalizes receipts."""

    def __init__(
        self, journal: BacktestMemoryJournal, writer: ArteJournalWriter, *,
        attempt_id: str, run_month: date, batch_size: int = 512,
        initial_sequence: int = 0, prior_batch_id: str = NIL_BATCH_ID,
        source_cursor: str = "start", expected_config: dict[str, Any] | None = None,
        fixed_market_parent_plan: object | None = None,
        fixed_market_execution_plan: object | None = None,
        expected_market_start: datetime | None = None,
        expected_market_plan_token: str | None = None,
        expected_query_sha256: str | None = None,
    ) -> None:
        attempt = str(UUID(attempt_id))
        prior = str(UUID(prior_batch_id))
        if (writer.run_id != journal.run_id or writer.run_mode != "backtest"
                or writer.journal_profile not in {"backtest_v2", "backtest_v3", "backtest_v4"}
                or writer.coalesce_batches
                or type(batch_size) is not int or not 1 <= batch_size <= writer.max_events_per_commit
                or type(initial_sequence) is not int or initial_sequence < 0
                or (initial_sequence == 0 and prior != NIL_BATCH_ID)
                or (initial_sequence > 0 and prior == NIL_BATCH_ID)
                or journal.latest_sequence(journal.run_id) < initial_sequence
                or run_month.day != 1 or not source_cursor):
            raise ValueError("Typed Backtest publisher lacks an exclusive bounded prefix")
        if writer.journal_profile == "backtest_v3":
            import re
            if (re.fullmatch(r"[0-9a-f]{64}", expected_market_plan_token or "") is None
                    or re.fullmatch(r"[0-9a-f]{64}", expected_query_sha256 or "") is None):
                raise ValueError("V3 publisher needs pinned squeeze authority")
        self.journal = journal
        self.writer = writer
        self.attempt_id = attempt
        self.run_month = run_month
        self.batch_size = batch_size
        self.expected_config = expected_config
        self.fixed_market_parent_plan = fixed_market_parent_plan
        self.fixed_market_execution_plan = fixed_market_execution_plan
        self._first_price_source = None
        self.expected_market_start = expected_market_start
        self.expected_market_plan_token = expected_market_plan_token
        self.expected_query_sha256 = expected_query_sha256
        self._sequence = initial_sequence
        self._batch_id = prior
        self._source_cursor = source_cursor
        self._task: asyncio.Task[TypedBacktestReceipt] | None = None
        self._terminal_task: asyncio.Task[TypedBacktestReceipt] | None = None
        self._error: BaseException | None = None
        self._checkpoint_waiters: list[tuple[
            int, str, date, object | None, object | None, object | None,
            object | None,
            tuple[CapturedPortfolioSnapshot, ...],
            tuple[dict[str, object], ...] | None,
            asyncio.Future[TypedBacktestReceipt],
        ]] = []
        # This is a performance index of already fenced source revisions, not
        # recovery authority. Cold resume must reload and verify ClickHouse.
        self._committed_strategy_intents: dict[str, tuple[TypedJournalBatch, object]] = {}
        self._committed_order_lineage: dict[str, tuple] = {}
        self._committed_order_lineage_proofs: dict[str, str] = {}
        self._committed_order_lineage_oms_records: dict[str, str] = {}

    def restore_verified_oms_sources(self, lineages, protection_history) -> None:
        """Seed pre-crash source indexes from normalized committed V4 facts."""
        from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
        from src.trading_runtime.arte_oms_projection import (
            RecoveredStrategyOneOmsLineage,
        )

        if (self.writer.journal_profile != "backtest_v4"
                or not isinstance(protection_history, CompleteProtectionHistory)
                or protection_history.run_id != self.journal.run_id
                or protection_history.through_sequence != self._sequence
                or self._committed_strategy_intents
                or self._committed_order_lineage
                or self.journal.pending_record_count):
            raise RuntimeError("Cold OMS sources require a clean verified V4 prefix")
        sources = {}
        orders = {}
        records = {}
        proofs = {}
        for lineage in lineages:
            if not isinstance(lineage, RecoveredStrategyOneOmsLineage):
                raise RuntimeError("Cold OMS source is not normalized")
            source = lineage.source_intent
            batch = getattr(source, "source_batch", None)
            group = lineage.state.group
            intent = getattr(source, "intent", None)
            if (not isinstance(batch, TypedJournalBatch)
                    or batch.run_id != self.journal.run_id
                    or batch.first_sequence != source.sequence
                    or batch.last_sequence > self._sequence
                    or batch.batch_id != source.batch_id
                    or batch.events[0]["record_id"] != source.record_id
                    or group["strategy_intent_id"] != intent.intent_id):
                raise RuntimeError("Cold OMS intent lacks its committed source batch")
            previous = sources.setdefault(intent.intent_id, (batch, intent))
            if previous != (batch, intent):
                raise RuntimeError("Cold OMS intent identity is conflicting")
            group_id = str(group["group_id"])
            oms_record_id = str(UUID(str(group["record_id"])))
            for order in lineage.orders:
                raw = dict(order.raw or {})
                if (set(raw) != {"canonical_strategy_id", "canonical_strategy_revision",
                                 "canonical_run_id", "canonical_metadata"}
                        or raw["canonical_run_id"] != self.journal.run_id
                        or raw["canonical_strategy_id"] != group["strategy_id"]
                        or raw["canonical_strategy_revision"] != group["strategy_revision"]
                        or not isinstance(raw["canonical_metadata"], dict)
                        or not order.cOID):
                    raise RuntimeError("Cold OMS order lacks verified canonical lineage")
                raw["strategy_id"] = raw.pop("canonical_strategy_id")
                key = order.cOID
                value = (raw, group["account_id"], order.ticker.upper(),
                         order.conid, group_id, intent.intent_id)
                if key in orders and orders[key] != value:
                    raise RuntimeError("Cold OMS order lineage repeats a client ID")
                orders[key] = value
                records[key] = oms_record_id
                meta = raw["canonical_metadata"]
                if meta.get("reason") == "structural_profit_target_advanced":
                    matching = [record for record in protection_history.records
                                if record.payload.get("order_group_id") == group_id
                                and record.payload.get("client_order_id") == key
                                and record.payload.get("kind") == "target"
                                and record.payload.get("phase") == "effective"
                                and record.payload.get("intent_id") ==
                                    meta.get("replacement_intent_id")
                                and record.payload.get("price") ==
                                    meta.get("target_price")]
                    if len(matching) != 1:
                        raise RuntimeError("Cold OMS amended order lacks one typed proof")
                    proofs[key] = str(UUID(matching[0].record_id))
        self._committed_strategy_intents = sources
        self._committed_order_lineage = orders
        self._committed_order_lineage_oms_records = records
        self._committed_order_lineage_proofs = proofs

    @property
    def fenced_sequence(self) -> int:
        return self._sequence

    @property
    def checkpoint_pending(self) -> bool:
        return bool(self._checkpoint_waiters)

    def enqueue_pending(self) -> asyncio.Task[TypedBacktestReceipt]:
        """Schedule projection and persistence without hot-path work or I/O.

        An active submission owns the current prefix. The caller may enqueue
        the next prefix after its checkpoint fence settles. Journal capacity
        bounds new event admission meanwhile; no disk fallback is allowed.
        """
        if self._error is not None:
            raise RuntimeError("Typed Backtest publication failed") from self._error
        if self._terminal_task is not None:
            raise RuntimeError("Typed Backtest terminal publication owns the suffix")
        if self._task is not None and not self._task.done():
            return self._task
        if self._task is not None:
            self._task.result()  # Surface an earlier failed receipt.
        target_sequence = (self._checkpoint_waiters[0][0]
                           if self._checkpoint_waiters else
                           self.journal.latest_sequence(self.journal.run_id))
        self._task = asyncio.create_task(self._drain(target_sequence=target_sequence))
        return self._task

    def bind_first_price_source(self, source: object) -> None:
        """Bind one native20 source before entry-prefix projection starts."""
        from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        from src.backend.backtest_market_data import CertifiedMarketDayPlan
        if (type(source) is not CertifiedPriceReadbackAuthority
                or source.run_id != self.journal.run_id
                or self.writer.journal_profile != 'backtest_v4'
                or not isinstance(self.expected_config, dict)
                or self.expected_config.get('strategy_revision') not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34)
                or self.expected_config.get('strategy_id') != 'early-squeeze-strategy'
                or type(self.fixed_market_parent_plan) is not CertifiedMarketDayPlan
                or source.plan.source.market.token != self.fixed_market_parent_plan.token
                or source.plan.source.market.sessions != self.fixed_market_parent_plan.sessions
                or self._first_price_source is not None):
            raise ValueError("Strategy20 publisher source lacks its exact unbound run")
        # run_month partitions the execution's creation date. Native entries
        # refer to the historical market session, which can be another month.
        self._first_price_source = source

    def _prepare_batches(self, through_sequence: int) -> tuple[
            TypedJournalBatch | V3SqueezeBatch | V4CompoundBatch
            | V4StrategyOneEntryBatch | V4FollowThroughFailureBatch
            | V4ProfitGivebackBatch
            | V4ConfirmedAhFailureBatch
            | V4LiquidityFadeFailureBatch
            | V4PortfolioAllocationBatch | V4ReservationReasonBatch
            | V4BrokerAcknowledgementBatch | V4OrderCancelBatch
            | V4OrderRepriceBatch | V4RiskActionBatch | V4ProtectionChangeBatch
            | V4ProtectionReconciliationBatch, ...]:
        """Project at most one commit-sized prefix outside the event loop."""
        if not self._sequence < through_sequence <= self._sequence + self.batch_size:
            raise ValueError("Typed Backtest projection exceeds one commit budget")
        if self.writer.journal_profile == "backtest_v4":
            units = project_pending_backtest_v4_prefix(
                self.journal, attempt_id=self.attempt_id,
                run_month=self.run_month, prior_sequence=self._sequence,
                prior_batch_id=self._batch_id, source_cursor=self._source_cursor,
                expected_config=self.expected_config,
                fixed_market_parent_plan=self.fixed_market_parent_plan,
                fixed_market_execution_plan=self.fixed_market_execution_plan,
                first_price_source=self._first_price_source,
                expected_market_start=self.expected_market_start,
                published_sources=dict(self._committed_strategy_intents),
                committed_order_lineage=dict(self._committed_order_lineage),
                committed_order_lineage_proofs=dict(self._committed_order_lineage_proofs),
                committed_order_lineage_oms_records=dict(
                    self._committed_order_lineage_oms_records),
                through_sequence=through_sequence)
            return _coalesce_v4_units(units, max_events=self.batch_size)
        if self.writer.journal_profile == "backtest_v3":
            from src.backend.backtest_squeeze_episode_v3 import coalesce_squeeze_units_v3
            units = project_pending_backtest_v3_prefix(
                self.journal, attempt_id=self.attempt_id,
                run_month=self.run_month, prior_sequence=self._sequence,
                prior_batch_id=self._batch_id, source_cursor=self._source_cursor,
                expected_config=self.expected_config,
                fixed_market_parent_plan=self.fixed_market_parent_plan,
                fixed_market_execution_plan=self.fixed_market_execution_plan,
                expected_market_start=self.expected_market_start,
                expected_market_plan_token=self.expected_market_plan_token,
                expected_query_sha256=self.expected_query_sha256,
                through_sequence=through_sequence)
            return tuple(coalesce_squeeze_units_v3(
                units[offset:offset + self.batch_size])
                for offset in range(0, len(units), self.batch_size))
        prefix = project_pending_backtest_prefix(
            self.journal, attempt_id=self.attempt_id,
            run_month=self.run_month, prior_sequence=self._sequence,
            prior_batch_id=self._batch_id, source_cursor=self._source_cursor,
            expected_config=self.expected_config,
            fixed_market_parent_plan=self.fixed_market_parent_plan,
            fixed_market_execution_plan=self.fixed_market_execution_plan,
            expected_market_start=self.expected_market_start,
            through_sequence=through_sequence,
        )
        batches = tuple(_coalesce_unpublished(
            prefix.batches[offset:offset + self.batch_size])
            for offset in range(0, len(prefix.batches), self.batch_size))
        return batches

    async def _drain(self, *, target_sequence: int | None = None) -> TypedBacktestReceipt:
        try:
            if target_sequence is None:
                target_sequence = (self._checkpoint_waiters[0][0]
                                   if self._checkpoint_waiters else
                                   self.journal.latest_sequence(self.journal.run_id))
            if target_sequence < self._sequence:
                raise ValueError("Typed Backtest drain target precedes its fence")
            while self._sequence < target_sequence:
                through_sequence = min(target_sequence,
                                       self._sequence + self.batch_size)
                batches = await asyncio.to_thread(self._prepare_batches,
                                                  through_sequence)
                if (not batches or (self.writer.journal_profile != "backtest_v4"
                                   and len(batches) != 1)):
                    raise RuntimeError("Typed Backtest projector changed the bounded prefix")
                for unit in batches:
                    batch = unit.base if isinstance(
                        unit, (V3SqueezeBatch, V4CompoundBatch,
                               V4StrategyOneEntryBatch, V4FollowThroughFailureBatch, V4ProfitGivebackBatch, V4ConfirmedAhFailureBatch, V4LiquidityFadeFailureBatch,
                               V4OmsTacticBatch,
                               V4PortfolioAllocationBatch, V4ReservationReasonBatch,
                               V4BrokerAcknowledgementBatch, V4OrderCancelBatch,
                               V4OrderRepriceBatch,
                               V4RiskActionBatch,
                               V4ProtectionChangeBatch,
                               V4ProtectionReconciliationBatch)) else unit
                    if (batch.first_sequence != self._sequence + 1
                            or batch.prior_batch_id != self._batch_id):
                        raise RuntimeError("Typed Backtest batch chain is not contiguous")
                    receipt = (self.writer.submit_compound_v4(unit,
                                    **({'first_price_source': self._first_price_source}
                                       if unit.children['profit_givebacks'] or unit.children['confirmed_ah_failures'] or unit.children['liquidity_fade_failures'] else {}))
                               if isinstance(unit, V4CompoundBatch)
                               else self.writer.submit_profit_exit_v4(unit,
                                    first_price_source=self._first_price_source)
                               if isinstance(unit, V4ProfitGivebackBatch)
                               else self.writer.submit_confirmed_ah_exit_v4(unit,
                                    first_price_source=self._first_price_source)
                               if isinstance(unit, V4ConfirmedAhFailureBatch)
                               else self.writer.submit_liquidity_fade_exit_v4(unit,
                                    first_price_source=self._first_price_source)
                               if isinstance(unit, V4LiquidityFadeFailureBatch)
                               else self.writer.submit_followthrough_exit_v4(unit)
                               if isinstance(unit, V4FollowThroughFailureBatch)
                               else self.writer.submit_strategy_one_entry_v4(unit)
                               if isinstance(unit, V4StrategyOneEntryBatch)
                               else self.writer.submit_oms_tactic_v4(unit)
                               if isinstance(unit, V4OmsTacticBatch)
                               else self.writer.submit_portfolio_allocation_v4(unit)
                               if isinstance(unit, V4PortfolioAllocationBatch)
                               else self.writer.submit_reservation_reason_v4(unit)
                               if isinstance(unit, V4ReservationReasonBatch)
                               else self.writer.submit_broker_acknowledgement_v4(unit)
                               if isinstance(unit, V4BrokerAcknowledgementBatch)
                               else self.writer.submit_order_cancel_v4(unit)
                               if isinstance(unit, V4OrderCancelBatch)
                               else self.writer.submit_order_reprice_v4(unit)
                               if isinstance(unit, V4OrderRepriceBatch)
                               else self.writer.submit_risk_action_v4(unit)
                               if isinstance(unit, V4RiskActionBatch)
                               else self.writer.submit_protection_change_v4(unit)
                               if isinstance(unit, V4ProtectionChangeBatch)
                               else self.writer.submit_protection_reconciliation_v4(unit)
                               if isinstance(unit, V4ProtectionReconciliationBatch)
                               else self.writer.submit_squeeze_v3(unit)
                               if isinstance(unit, V3SqueezeBatch)
                               else self.writer.submit_base_v4(batch)
                               if self.writer.journal_profile == "backtest_v4"
                               else self.writer.submit(batch))
                    committed = await asyncio.wrap_future(receipt)
                    if str(UUID(str(committed))) != batch.batch_id:
                        raise RuntimeError("Typed Backtest writer changed an exclusive batch ID")
                    for source_unit in (unit.units if isinstance(
                            unit, V4CompoundBatch) else (unit,)):
                        source_batch = (source_unit if isinstance(source_unit,
                            TypedJournalBatch) else source_unit.base)
                        if isinstance(source_unit, V4StrategyOneEntryBatch):
                            from src.trading_runtime.strategy_one_intent import (
                                strategy_one_add_intent,
                                strategy_one_entry_intent,
                            )

                            parent_id = source_unit.base.events[0]["record_id"]
                            sidecar = (self.journal.strategy_one_entry_for_record(parent_id)
                                       if source_unit.entry_evidence else
                                       self.journal.strategy_one_add_for_record(parent_id))
                            if sidecar is None:
                                raise RuntimeError("Committed Strategy 1 acquisition lost its source")
                            proposal, session_date = sidecar
                            if source_unit.entry_evidence and proposal.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34):
                                from .backtest_strategy_certified_price_break import (
                                    CertifiedPriceReadbackAuthority, certified_price_entry_intent,
                                )
                                source = self._first_price_source
                                if (type(source) is not CertifiedPriceReadbackAuthority
                                        or source.run_id != self.journal.run_id):
                                    raise ValueError('Committed native entry lacks its exact source')
                                intent = certified_price_entry_intent(
                                    source.plan, proposal, session_date=session_date)
                            else:
                                intent = (strategy_one_entry_intent(
                                    proposal, session_date=session_date)
                                    if source_unit.entry_evidence else
                                    strategy_one_add_intent(
                                        proposal, session_date=session_date))
                            self._committed_strategy_intents[intent.intent_id] = (
                                _committed_intent_source(
                                    batch, source_unit.base.events[0]["record_id"]),
                                intent)
                        elif isinstance(source_unit, V4FollowThroughFailureBatch):
                            parent_id = source_unit.base.events[0]["record_id"]
                            sidecar = self.journal.followthrough_exit_for_record(parent_id)
                            if sidecar is None:
                                raise RuntimeError("Committed failure exit lost its immutable scalar source")
                            intent, _, _ = sidecar
                            self._committed_strategy_intents[intent.intent_id] = (
                                _committed_intent_source(batch, parent_id), intent)
                        elif isinstance(source_unit, V4ProfitGivebackBatch):
                            parent_id = source_unit.base.events[0]['record_id']
                            sidecar = self.journal.profit_giveback_exit_for_record(parent_id)
                            if sidecar is None:
                                raise RuntimeError('Committed profit exit lost its immutable scalar source')
                            intent, _, _, _ = sidecar
                            self._committed_strategy_intents[intent.intent_id] = (
                                _committed_intent_source(batch, parent_id), intent)
                        elif isinstance(source_unit, V4ConfirmedAhFailureBatch):
                            parent_id = source_unit.base.events[0]['record_id']
                            sidecar = self.journal.confirmed_ah_exit_for_record(parent_id)
                            if sidecar is None:
                                raise RuntimeError('Committed AH confirmation lost its immutable source')
                            intent, _, _, _, _ = sidecar
                            self._committed_strategy_intents[intent.intent_id] = (
                                _committed_intent_source(batch, parent_id), intent)
                        elif isinstance(source_unit, V4LiquidityFadeFailureBatch):
                            parent_id = source_unit.base.events[0]['record_id']
                            sidecar = self.journal.liquidity_fade_exit_for_record(parent_id)
                            if sidecar is None:
                                raise RuntimeError('Committed liquidity exit lost its immutable source')
                            intent = sidecar[0]
                            self._committed_strategy_intents[intent.intent_id] = (
                                _committed_intent_source(batch, parent_id), intent)
                        elif (self.writer.journal_profile == "backtest_v4"
                              and source_batch.first_sequence == source_batch.last_sequence
                              and len(source_batch.events) == 1):
                            intent = self.journal.strategy_one_protection_for_record(
                                source_batch.events[0]["record_id"])
                            if intent is None:
                                intent = self.journal.numbered_session_exit_for_record(
                                    source_batch.events[0]["record_id"])
                            if intent is not None:
                                self._committed_strategy_intents[intent.intent_id] = (
                                    _committed_intent_source(
                                        batch, source_batch.events[0]["record_id"]),
                                    intent)
                        if (self.writer.journal_profile == "backtest_v4"
                                and len(source_batch.events) == 1
                                and source_batch.events[0]["entity_type"] == "order_group_state"):
                            from src.backend.backtest_typed_projection import (
                                authorized_oms_lineage_transition,
                                committed_oms_order_lineage,
                            )
                            event = source_batch.events[0]
                            group = self.journal.oms_group_for_record(event["record_id"])
                            if group is None:
                                raise RuntimeError("Committed OMS order lost its frozen lineage")
                            source_record, = self.journal.unfenced_records(
                                after_sequence=source_batch.first_sequence - 1,
                                through_sequence=source_batch.last_sequence)
                            if source_record.record_id != event["record_id"]:
                                raise RuntimeError("Committed OMS order changed journal identity")
                            protection_proof = self.journal.oms_effective_protection_for_record(
                                source_record)
                            for key, lineage in committed_oms_order_lineage(
                                    group, run_id=source_batch.run_id,
                                    strategy_id=source_batch.oms_group_states[0]["strategy_id"],
                                    strategy_revision=source_batch.oms_group_states[0]["strategy_revision"],
                                    authorized_protection=protection_proof).items():
                                if (key in self._committed_order_lineage
                                        and self._committed_order_lineage[key] != lineage
                                        and not authorized_oms_lineage_transition(
                                            self._committed_order_lineage[key], lineage,
                                            client_order_id=key,
                                            proof=protection_proof.get(f"target:{key}"))):
                                    raise RuntimeError("Committed OMS order lineage changed without typed amendment")
                                self._committed_order_lineage[key] = lineage
                                self._committed_order_lineage_oms_records[key] = str(UUID(
                                    source_record.record_id))
                                proof = protection_proof.get(f"target:{key}")
                                if (lineage[0]["canonical_metadata"].get("reason") ==
                                        "structural_profit_target_advanced"):
                                    if proof is None:
                                        raise RuntimeError("Committed amended OMS lineage lost its proof")
                                    self._committed_order_lineage_proofs[key] = str(UUID(
                                        proof.record_id))
                                else:
                                    self._committed_order_lineage_proofs.pop(key, None)
                    self.journal.mark_fenced(batch.last_sequence)
                    self._sequence = batch.last_sequence
                    self._batch_id = batch.batch_id
                    self._source_cursor = batch.source_cursor
                    current = TypedBacktestReceipt(self._sequence, self._batch_id,
                                                   self._source_cursor)
                    remaining = []
                    for sequence, cursor, session_date, manager_state, broker_state, oms_observations, evidence_state, portfolio_captures, campaign_ownership, waiter in self._checkpoint_waiters:
                        if sequence <= self._sequence:
                            if not waiter.done():
                                if (sequence != self._sequence
                                        or cursor != self._source_cursor):
                                    waiter.set_exception(RuntimeError(
                                        "Typed Backtest checkpoint cursor differs from committed prefix"))
                                else:
                                    if manager_state is not None:
                                        manager_receipt = self.writer.submit_manager_snapshot(
                                            session_date=session_date,
                                            checkpoint_sequence=sequence,
                                            journal_batch_id=self._batch_id,
                                            state=manager_state,
                                            **({} if self._first_price_source is None else
                                               {'first_price_source': self._first_price_source}))
                                        if await asyncio.wrap_future(manager_receipt) != self._batch_id:
                                            raise RuntimeError(
                                                "Manager snapshot differs from committed checkpoint")
                                    if broker_state is not None:
                                        broker_receipt = self.writer.submit_broker_match_snapshot(
                                            session_date=session_date,
                                            checkpoint_sequence=sequence,
                                            boundary_ms=broker_state[0],
                                            journal_batch_id=self._batch_id,
                                            state=broker_state[1],
                                            **({} if self._first_price_source is None else
                                               {'first_price_source': self._first_price_source}))
                                        if await asyncio.wrap_future(broker_receipt) != self._batch_id:
                                            raise RuntimeError(
                                                "Broker snapshot differs from committed checkpoint")
                                    if oms_observations is not None:
                                        oms_receipt = self.writer.submit_oms_observation_snapshot(
                                            session_date=session_date,
                                            checkpoint_sequence=sequence,
                                            boundary_ms=broker_state[0],
                                            journal_batch_id=self._batch_id,
                                            groups=oms_observations,
                                            **({} if self._first_price_source is None else
                                               {'first_price_source': self._first_price_source}))
                                        if await asyncio.wrap_future(oms_receipt) != self._batch_id:
                                            raise RuntimeError(
                                                "OMS observation differs from committed checkpoint")
                                    if evidence_state is not None:
                                        evidence_receipt = self.writer.submit_evidence_snapshot(
                                            session_date=session_date,
                                            checkpoint_sequence=sequence,
                                            journal_batch_id=self._batch_id,
                                            state=evidence_state,
                                            **({} if self._first_price_source is None else
                                               {'first_price_source': self._first_price_source}))
                                        if await asyncio.wrap_future(evidence_receipt) != self._batch_id:
                                            raise RuntimeError(
                                                "Evidence snapshot differs from committed checkpoint")
                                    for capture in portfolio_captures:
                                        snapshot_receipt = self.writer.submit_running_portfolio_snapshot(
                                            journal_batch_id=self._batch_id,
                                            captured=capture)
                                        if await asyncio.wrap_future(snapshot_receipt) != self._batch_id:
                                            raise RuntimeError(
                                                "Portfolio snapshot differs from committed checkpoint")
                                    if campaign_ownership is not None:
                                        campaign_receipt = self.writer.submit_campaign_snapshot(
                                            session_date=session_date,
                                            checkpoint_sequence=sequence,
                                            boundary_ms=broker_state[0],
                                            journal_batch_id=self._batch_id,
                                            ownership=campaign_ownership,
                                            **({} if self._first_price_source is None else
                                               {'first_price_source': self._first_price_source}))
                                        if await asyncio.wrap_future(campaign_receipt) != self._batch_id:
                                            raise RuntimeError(
                                                "Campaign snapshot differs from committed checkpoint")
                                    waiter.set_result(current)
                        else:
                            remaining.append((sequence, cursor, session_date,
                                              manager_state, broker_state, oms_observations,
                                              evidence_state,
                                              portfolio_captures, campaign_ownership, waiter))
                    self._checkpoint_waiters = remaining
            return TypedBacktestReceipt(self._sequence, self._batch_id,
                                        self._source_cursor)
        except BaseException as exc:
            self._error = exc
            for _, _, _, _, _, _, _, _, _, waiter in self._checkpoint_waiters:
                if not waiter.done():
                    waiter.set_exception(exc)
            self._checkpoint_waiters.clear()
            raise

    async def _drain_after_active(
        self, active: asyncio.Task[TypedBacktestReceipt],
        waiter: asyncio.Future[TypedBacktestReceipt],
    ) -> TypedBacktestReceipt:
        """Chain a checkpoint after an in-flight prefix without waiting in engine."""
        await asyncio.shield(active)
        if waiter.done():
            return waiter.result()
        return await self._drain()

    @staticmethod
    def _observe_chained_failure(task: asyncio.Task[TypedBacktestReceipt]) -> None:
        # The checkpoint receipt is the caller-facing failure channel. Reading
        # the duplicate chained-task exception prevents a spurious unhandled
        # task report; awaiting this task still raises the same exception.
        if not task.cancelled():
            task.exception()

    def enqueue_checkpoint(self, *, boundary_id: str,
                           status: str = "running",
                           manager_state: object | None = None,
                           broker_state: object | None = None,
                           oms_observations: object | None = None,
                           evidence_state: object | None = None,
                           portfolio_captures: tuple[CapturedPortfolioSnapshot, ...] = (),
                           campaign_ownership: tuple[dict[str, object], ...] | None = None,
                           ) -> asyncio.Future[TypedBacktestReceipt]:
        """Return immediately; resolve only after this exact cursor is durable."""
        if status != "running":
            raise ValueError("Terminal Backtest needs lifecycle-last typed account captures")
        if self._terminal_task is not None:
            raise RuntimeError("Terminal publication owns the Backtest suffix")
        if self._checkpoint_waiters:
            raise RuntimeError("A typed Backtest checkpoint is already pending")
        active = self._task if self._task is not None and not self._task.done() else None
        pending = self.journal.unfenced_records()
        if (not pending or not boundary_id
                or (pending[-1].category, pending[-1].entity_type,
                    pending[-1].entity_id) !=
                   ("checkpoint", "market_boundary", boundary_id)):
            raise ValueError("Typed Backtest checkpoint needs the last normalized cursor")
        if manager_state is not None:
            from src.backend.backtest_strategy_one_management import (
                StrategyOneManagementState,
            )
            if (self.writer.journal_profile != "backtest_v4"
                    or not isinstance(manager_state, StrategyOneManagementState)
                    or manager_state.boundary_ms != pending[-1].payload.get(
                        "boundary_ms")):
                raise ValueError("Manager capture differs from checkpoint boundary")
        if broker_state is not None:
            if (self.writer.journal_profile != "backtest_v4"
                    or not isinstance(broker_state, tuple)
                    or len(broker_state) != 2
                    or broker_state[0] != pending[-1].payload.get("boundary_ms")
                    or not isinstance(broker_state[1], dict)):
                raise ValueError("Broker capture differs from checkpoint boundary")
        if oms_observations is not None:
            from src.trading_runtime.strategy_one_oms_observation_snapshot import (
                OmsObservedGroup,
            )
            if (self.writer.journal_profile != "backtest_v4"
                    or broker_state is None
                    or not isinstance(oms_observations, dict)
                    or any(not isinstance(group_id, str) or not group_id
                           or not isinstance(group, OmsObservedGroup)
                           for group_id, group in oms_observations.items())):
                raise ValueError("OMS observation capture differs from checkpoint boundary")
        if evidence_state is not None:
            from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
            if (self.writer.journal_profile != "backtest_v4"
                    or not isinstance(evidence_state, StrategyOneEvidenceState)
                    or evidence_state.boundary_ms != pending[-1].payload.get("boundary_ms")):
                raise ValueError("Evidence capture differs from checkpoint boundary")
        if (not isinstance(portfolio_captures, tuple)
                or any(not isinstance(capture, CapturedPortfolioSnapshot)
                       or capture.run_id != self.journal.run_id
                       or capture.state_revision != pending[-1].sequence
                       or capture.snapshot_at != pending[-1].event_time
                       for capture in portfolio_captures)
                or len({capture.account_id for capture in portfolio_captures})
                   != len(portfolio_captures)):
            raise ValueError("Portfolio captures differ from checkpoint boundary")
        if campaign_ownership is not None:
            if (broker_state is None or self.writer.journal_profile != "backtest_v4"
                    or not isinstance(campaign_ownership, tuple)
                    or any(not isinstance(owner, dict) for owner in campaign_ownership)):
                raise ValueError("Campaign capture needs a V4 broker boundary")
        session_date = date.fromisoformat(pending[-1].payload["session_date"])
        waiter: asyncio.Future[TypedBacktestReceipt] = asyncio.get_running_loop().create_future()
        self._checkpoint_waiters.append((pending[-1].sequence, boundary_id,
                                         session_date, manager_state,
                                         broker_state, oms_observations,
                                         evidence_state,
                                         portfolio_captures, campaign_ownership, waiter))
        try:
            if active is None:
                self.enqueue_pending()
            else:
                self._task = asyncio.create_task(
                    self._drain_after_active(active, waiter))
                self._task.add_done_callback(self._observe_chained_failure)
        except BaseException:
            self._checkpoint_waiters.pop()
            waiter.cancel()
            raise
        return waiter

    async def await_fence(self) -> TypedBacktestReceipt:
        """Wait once at a checkpoint boundary; never once per journal event."""
        if self._task is None:
            self.enqueue_pending()
        assert self._task is not None
        return await asyncio.shield(self._task)

    async def fence_checkpoint(
        self, *, boundary_id: str, status: str = "running",
        manager_state: object | None = None,
        broker_state: object | None = None,
        oms_observations: object | None = None,
        evidence_state: object | None = None,
        portfolio_captures: tuple[CapturedPortfolioSnapshot, ...] = (),
        campaign_ownership: tuple[dict[str, object], ...] | None = None,
    ) -> TypedBacktestReceipt:
        """Fence one normalized completed cursor, never an opaque state map.

        Terminal publication must instead place the lifecycle event last and
        attach every typed account capture to that exact event batch. The
        current controller ordering does not yet meet that contract.
        """
        return await asyncio.shield(self.enqueue_checkpoint(
            boundary_id=boundary_id, status=status,
            manager_state=manager_state, broker_state=broker_state,
            oms_observations=oms_observations,
            evidence_state=evidence_state,
            portfolio_captures=portfolio_captures,
            campaign_ownership=campaign_ownership))

    def enqueue_terminal(
        self, captures: tuple[CapturedPortfolioSnapshot, ...],
    ) -> asyncio.Task[TypedBacktestReceipt]:
        """Hand a lifecycle-last V4 suffix to the worker without network I/O."""
        if (self.writer.journal_profile != "backtest_v4"
                or self._terminal_task is not None or self._error is not None
                or not isinstance(captures, tuple) or not captures
                or any(type(row) is not CapturedPortfolioSnapshot
                       or row.run_id != self.journal.run_id for row in captures)):
            raise ValueError("V4 terminal needs unique typed account captures")
        records = self.journal.unfenced_records()
        if (not records or (records[-1].category, records[-1].entity_type,
                            records[-1].entity_id) !=
                ("lifecycle", "run", self.journal.run_id)
                or records[-1].payload.get("status") not in
                {"completed", "stopped", "failed"}):
            raise ValueError("V4 terminal needs the last lifecycle record")
        terminal_sequence = records[-1].sequence
        self._terminal_task = asyncio.create_task(
            self._publish_terminal_v4(terminal_sequence, captures))
        return self._terminal_task

    async def _publish_terminal_v4(
        self, sequence: int, captures: tuple[CapturedPortfolioSnapshot, ...],
    ) -> TypedBacktestReceipt:
        try:
            if self._task is not None:
                await asyncio.shield(self._task)
            pending = self.journal.unfenced_records(
                after_sequence=self._sequence, through_sequence=sequence)
            snapshot_start = len(pending) - 1
            while snapshot_start > 0 and (
                pending[snapshot_start - 1].category,
                pending[snapshot_start - 1].entity_type,
            ) in {("snapshot", "portfolio"), ("snapshot", "position")}:
                snapshot_start -= 1
            if snapshot_start == len(pending) - 1:
                raise RuntimeError(
                    "V4 terminal lacks normalized broker account snapshots")
            first_terminal_sequence = pending[snapshot_start].sequence
            if self._sequence < first_terminal_sequence - 1:
                await self._drain(target_sequence=first_terminal_sequence - 1)
            if self._sequence != first_terminal_sequence - 1:
                raise RuntimeError("V4 terminal predecessor is not fully fenced")
            from src.backend.backtest_terminal_broker_snapshot_v4 import (
                project_v4_terminal_broker_batch,
            )

            unit = await asyncio.to_thread(
                project_v4_terminal_broker_batch,
                tuple(pending[snapshot_start:]), run_id=self.journal.run_id,
                account_ids=tuple(row.account_id for row in captures),
                attempt_id=self.attempt_id, run_month=self.run_month,
                prior_batch_id=self._batch_id,
                source_cursor=self._source_cursor)
            batch = unit.base
            submitted = self.writer.submit_terminal_backtest(
                batch, captures, unit.broker_snapshots,
                **({"first_price_source": self._first_price_source}
                   if self._first_price_source is not None else {}))
            committed = await asyncio.wrap_future(submitted)
            if str(UUID(str(committed))) != batch.batch_id:
                raise RuntimeError("V4 terminal writer changed the batch ID")
            self.journal.mark_fenced(sequence)
            self._sequence = sequence
            self._batch_id = batch.batch_id
            self._source_cursor = batch.source_cursor
            return TypedBacktestReceipt(sequence, batch.batch_id,
                                        batch.source_cursor)
        except BaseException as exc:
            self._error = exc
            raise

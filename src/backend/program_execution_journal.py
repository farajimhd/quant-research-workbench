"""Bounded transient program facts with native normalized V4 receipt fences.

The in-memory buffer is never durable authority. Only the concrete installed
program writer can advance its fence after an acknowledged native commit.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from copy import deepcopy
from types import MappingProxyType
from uuid import UUID

from .backtest_journal_memory import BacktestMemoryJournal
from .qmd_columnar_history_transport import _owned_worker


@dataclass(frozen=True, slots=True)
class ProgramJournalReceipt:
    run_id: str
    last_sequence: int
    batch_id: str
    source_cursor: str


@dataclass(frozen=True, slots=True)
class ProgramIntentSource:
    intent: object
    assignment_id: str
    record_id: str
    batch_id: str
    content_hash: str
    batch: object


@dataclass(frozen=True, slots=True)
class ProgramOmsSource:
    record_id: str
    batch_id: str
    lineage: object


class ProgramExecutionJournal:
    """Storage-neutral staging; intentionally not a Backtest journal subclass."""

    _BUFFER_METHODS = frozenset({
        'append', 'append_many', 'append_once', 'append_once_many',
        'append_strategy_order_command', 'append_oms_group_transition',
        'order_request_for_record', 'oms_group_for_record', 'oms_admission_for_record',
        'oms_effective_protection_for_record', 'records', 'unfenced_records',
        'latest_sequence', 'next_record_after_time', 'protection_records',
        'retain_rule_decision_basis', 'rule_decision_basis_for_record',
        'reference_json', 'reference_evidence', 'portfolio_states', 'portfolio_reservation',
        'portfolio_admission_reservation', 'strategy_assignment', 'strategy_assignments',
        'order_management_states', 'command_checkpoint',
    })

    def __init__(self, *_args, **_kwargs):
        raise TypeError('Program journal must be opened from its native owned writer')

    @classmethod
    async def from_native(cls, writer, *, max_pending_records=65536):
        from src.trading_runtime.arte_journal_writer import ArteJournalWriter
        from src.trading_runtime.arte_journal_commit_v4 import load_writer_v4_snapshot_prefix, V4CommittedPrefix
        from .program_v4_principal import ProgramV4WriterClient, ProgramV4KeeperLease
        if (cls is not ProgramExecutionJournal or type(writer) is not ArteJournalWriter
                or writer.journal_profile != 'live_v4' or type(writer._client) is not ProgramV4WriterClient
                or type(writer._live_v4_lease) is not ProgramV4KeeperLease
                or writer._run_mode not in {'replay', 'paper', 'live'}
                or writer._accepted_writes or writer._closed or writer._error is not None
                or type(max_pending_records) is not int or not 1 <= max_pending_records <= 65536):
            raise ValueError('Program journal requires a fresh concrete native program writer')
        writer._live_v4_lease.assert_current()
        prefix = await _owned_worker(load_writer_v4_snapshot_prefix, writer._client, writer._run_id)
        writer._live_v4_lease.assert_current()
        if prefix is None:
            # The native cold verifier proved absence of a committed event
            # chain; the writer already verified this run's sealed context.
            prefix = V4CommittedPrefix(writer._run_id, 0, str(UUID(int=0)), '', 'running', ())
        if (type(prefix) is not V4CommittedPrefix or prefix.run_id != writer._run_id
                or prefix.status != 'running' or writer._accepted_writes
                or writer._closed or writer._error is not None):
            raise ValueError('Program journal lost its verified running prefix during assembly')
        result = object.__new__(cls)
        result._writer = writer
        result._client = writer._client
        result._lease = writer._live_v4_lease
        result._mode = writer._run_mode
        result._buffer = BacktestMemoryJournal(run_id=prefix.run_id,
            initial_sequence=prefix.last_sequence, max_pending_records=max_pending_records)
        result._sequence, result._batch_id = prefix.last_sequence, prefix.last_batch_id
        result._source_cursor = prefix.source_cursor
        result._failure = None
        result._program_intent_inputs = {}
        result._program_intent_sources = {}
        result._program_oms_sources = {}
        result._program_modify_inputs = {}
        result._program_portfolio_engine = None
        result._lock = asyncio.Lock()
        result._closed = False
        return result

    @property
    def writer(self):
        return self._writer

    @property
    def run_id(self):
        return self._buffer.run_id

    @property
    def mode(self):
        return self._mode

    @property
    def fenced_sequence(self):
        return self._sequence

    @property
    def pending_record_count(self):
        return self._buffer.pending_record_count

    @property
    def max_pending_records(self):
        return self._buffer.max_pending_records

    @property
    def fenced_receipt(self):
        return ProgramJournalReceipt(self.run_id, self._sequence, self._batch_id, self._source_cursor)

    @property
    def live_counts(self):
        return self._buffer.live_counts

    def require_current(self):
        if self._closed or self._failure is not None:
            raise RuntimeError('Program journal requires cold recovery after closure or uncertain publication') from self._failure
        if (self._writer._closed or self._writer._error is not None
                or self._writer._run_mode != self._mode or self._writer._run_id != self.run_id
                or self._writer.journal_profile != 'live_v4'
                or self._writer._client is not self._client
                or self._writer._live_v4_lease is not self._lease):
            raise RuntimeError('Program journal lost its native writer authority')
        self._writer._live_v4_lease.assert_current()

    async def retain_program_admission(self, authority, engine, outcome):
        """Seed OMS only from original facts held by an exact durable publisher."""
        from src.trading_runtime.program_portfolio_transitions import (
            ProgramPortfolioTransitionAuthority, ProgramPortfolioAdmissionResult)
        from copy import deepcopy
        if (type(authority) is not ProgramPortfolioTransitionAuthority
                or type(outcome) is not ProgramPortfolioAdmissionResult
                or authority.writer is not self.writer or self.pending_record_count):
            raise TypeError('Program admission requires its native empty journal and publisher custody')
        self.require_current()
        records = await authority.consume_admission_source(engine, outcome)
        if self._program_portfolio_engine is not None and self._program_portfolio_engine is not engine:
            raise RuntimeError('Program journal cannot replace its certified Portfolio custody')
        self._program_portfolio_engine = engine
        creations = [payload for kind, _, account, payload in records
                     if kind == 'portfolio_reservation' and account == outcome.receipt.account_id
                     and payload.get('event') == 'reservation_created']
        if outcome.approved_intent is None:
            if creations:
                raise RuntimeError('Rejected native admission unexpectedly created capital reservation')
            return
        if len(creations) != 1 or creations[0]['intent_id'] != outcome.approved_intent.intent_id:
            raise RuntimeError('Native admission differs from original committed reservation fact')
        row = creations[0]
        key = (outcome.receipt.account_id, row['reservation_id'])
        with self._buffer._lock:
            if key in self._buffer._reservation_creations:
                raise RuntimeError('Native admission source was already installed')
            self._buffer._reservation_creations[key] = deepcopy(row)

    def portfolio_reservation(self, account_id, reservation_id):
        """Read the actual typed engine whose admission receipt was verified.

        The native assembly serializes admission, OMS and later typed state
        transitions. No transient Backtest snapshot becomes financial authority.
        """
        from dataclasses import asdict
        from src.trading_runtime.portfolio import PortfolioManagementEngine
        self.require_current()
        engine = self._program_portfolio_engine
        if engine is None:
            return None
        if (type(engine) is not PortfolioManagementEngine or engine.run_id != self.run_id
                or not engine._typed_recovery or engine._typed_backtest_recovery
                or engine._typed_admission_poisoned):
            raise RuntimeError('Program reservation lost its certified native Portfolio custody')
        reservation = engine.reservations.get(reservation_id)
        return deepcopy(asdict(reservation)) if reservation is not None and reservation.account_id == account_id else None

    def save_order_management_state(self, group_id, *, run_id, account_id, state):
        """Bounded transient OMS lookup; normalized group receipts own recovery."""
        self.require_current()
        if group_id not in self._buffer._order_states and len(self._buffer._order_states) >= self.max_pending_records:
            raise ValueError('Program transient OMS state exceeds its explicit object bound')
        self._buffer.save_order_management_state(group_id, run_id=run_id, account_id=account_id, state=state)

    def append_program_intent(self, intent, *, account_id, assignment_id,
                              strategy_id=None, strategy_revision=None):
        from src.trading_runtime.signals import StrategyIntent
        from src.trading_runtime.arte_intent_projection import project_strategy_intent
        self.require_current()
        registration = self._lease.registration
        identity = (registration.strategy_id if strategy_id is None else strategy_id,
                    registration.revision if strategy_revision is None else strategy_revision)
        if (type(intent) is not StrategyIntent or intent.metadata or identity != registration.key
                or type(account_id) is not str or not account_id
                or type(assignment_id) is not str or not assignment_id
                or len(self._program_intent_inputs) >= self.max_pending_records):
            raise ValueError('Program semantic intent lacks its actual typed source and assignment')
        original = deepcopy(intent)
        project_strategy_intent(original)
        record = self._buffer.append(run_id=self.run_id, category='strategy', entity_type='strategy_intent',
            entity_id=original.intent_id, account_id=account_id, event_time=original.event_time,
            payload={**original.payload(), 'strategy_id': identity[0], 'strategy_revision': identity[1]})
        self._program_intent_inputs[record.record_id] = (original, assignment_id)
        return record

    def program_intent_for_record(self, record_id):
        self.require_current()
        source = self._program_intent_inputs.get(record_id)
        return (deepcopy(source[0]), source[1]) if source is not None else None

    def program_intent_source(self, intent_id, account_id):
        self.require_current()
        source = self._program_intent_sources.get((intent_id, account_id))
        if source is None:
            raise ValueError('Program intent has no native committed source receipt')
        return ProgramIntentSource(deepcopy(source.intent), source.assignment_id, source.record_id,
                                   source.batch_id, source.content_hash, source.batch)

    def program_oms_source(self, group_id, account_id):
        self.require_current()
        source = self._program_oms_sources.get((group_id, account_id))
        if source is None:
            raise ValueError('Program OMS group has no native committed source receipt')
        return source

    def append_program_order_modify(self, *, request, **source):
        from src.trading_runtime.ibkr_schema import OrderRequest
        self.require_current()
        if (type(request) is not OrderRequest or source.get('run_id') != self.run_id
                or source.get('category') != 'command' or source.get('entity_type') != 'order_modify'
                or source.get('account_id') != request.acctId
                or len(self._program_modify_inputs) >= self.max_pending_records):
            raise ValueError('Program amendment lacks its actual typed replacement request')
        record = self._buffer.append(**source)
        self._program_modify_inputs[record.record_id] = deepcopy(request)
        return record

    def program_order_modify_for_record(self, record_id):
        self.require_current()
        return deepcopy(self._program_modify_inputs.get(record_id))

    def __getattr__(self, name):
        if name not in self._BUFFER_METHODS:
            raise AttributeError(name)
        def staged(*args, **kwargs):
            self.require_current()
            return getattr(self._buffer, name)(*args, **kwargs)
        return staged

    async def refresh_native_prefix(self):
        """Adopt external typed Portfolio receipts only into an empty buffer.

        The native assembly serializes this operation with decision, broker
        callback and OMS mutations. Pending source facts are never discarded.
        """
        from src.trading_runtime.arte_journal_commit_v4 import load_writer_v4_snapshot_prefix, V4CommittedPrefix
        self.require_current()
        if self.pending_record_count or self._lock.locked():
            self._failure = RuntimeError('External native prefix adoption requires an empty idle program buffer')
            raise self._failure
        async with self._lock:
            try:
                before = (self._sequence, self._batch_id)
                prefix = await _owned_worker(load_writer_v4_snapshot_prefix, self._writer._client, self.run_id)
                self.require_current()
                if (type(prefix) is not V4CommittedPrefix or prefix.run_id != self.run_id
                        or prefix.status != 'running' or prefix.last_sequence < before[0]
                        or before[0] > 0 and before[1] not in prefix.batch_ids
                        or self.pending_record_count or (self._sequence, self._batch_id) != before):
                    raise RuntimeError('External typed receipts differ from the empty owned program prefix')
                with self._buffer._lock:
                    if self._buffer._records or self._buffer.pending_record_count:
                        raise RuntimeError('Program facts arrived during native prefix refresh')
                    # These are only the storage-neutral buffer's cursor
                    # counters; native cold verification owns the new head.
                    self._buffer._base_sequence = prefix.last_sequence
                    self._buffer._next_sequence = prefix.last_sequence
                    self._buffer._fenced_sequence = prefix.last_sequence
                self._sequence, self._batch_id = prefix.last_sequence, prefix.last_batch_id
                self._source_cursor = prefix.source_cursor
                return ProgramJournalReceipt(self.run_id, self._sequence, self._batch_id, self._source_cursor)
            except BaseException as exc:
                self._failure = exc
                raise

    async def commit_native(self, unit):
        """Acknowledge only a contiguous exact pending prefix through native I/O."""
        from src.trading_runtime.arte_journal_writer import (
            TypedJournalBatch, V4OrderCancelBatch, V4OrderModifyCommandBatch,
            V4ProtectionChangeBatch, V4BrokerAcknowledgementBatch,
        )
        from src.trading_runtime.arte_journal_compound_v4 import V4CompoundBatch
        from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
        from src.trading_runtime.arte_order_exact_prices import ExactPriceOrderCommandBatch
        from src.trading_runtime.arte_execution_exact_prices import ExactPriceExecutionBatch
        from src.trading_runtime.arte_oms_exact_prices import ExactPriceOmsStateBatch
        from src.trading_runtime.arte_broker_policy_v4 import V4BrokerPolicyBatch
        submissions = {
            TypedJournalBatch: 'submit_base_v4', V4CompoundBatch: 'submit_compound_v4',
            QualifiedRuleDecisionBatch: 'submit_qualified_rule_v4',
            ExactPriceOrderCommandBatch: 'submit_exact_order_v4',
            ExactPriceExecutionBatch: 'submit_exact_execution_v4', ExactPriceOmsStateBatch: 'submit_exact_oms_v4',
            V4BrokerPolicyBatch: 'submit_broker_policy_v4',
            V4OrderCancelBatch: 'submit_order_cancel_v4', V4OrderModifyCommandBatch: 'submit_order_modify_command_v4',
            V4ProtectionChangeBatch: 'submit_protection_change_v4',
            V4BrokerAcknowledgementBatch: 'submit_broker_acknowledgement_v4',
        }
        self.require_current()
        if type(unit) not in submissions or self._lock.locked():
            raise ValueError('Program journal requires one bounded native publication at a time')
        async with self._lock:
            self.require_current()
            base = unit if type(unit) is TypedJournalBatch else unit.base
            completions = {row['record_id'] for row in base.signals
                           if row.get('signal_type') == 'program_boundary_completed'}
            children = unit.units if type(unit) is V4CompoundBatch else (unit,)
            qualified = {child.unit.basis['record_id'] for child in children
                         if type(child) is QualifiedRuleDecisionBatch}
            if completions != qualified:
                raise ValueError('Program completion publication requires exact native decision qualification')
            pending = self._buffer.unfenced_records(after_sequence=self._sequence,
                                                    through_sequence=base.last_sequence)
            if (base.run_id != self.run_id or base.first_sequence != self._sequence + 1
                    or base.prior_batch_id != self._batch_id or base.status != 'running'
                    or not pending or base.last_sequence != pending[-1].sequence
                    or [(str(UUID(str(e['record_id']))), e['sequence']) for e in base.events]
                       != [(str(UUID(r.record_id)), r.sequence) for r in pending]):
                raise ValueError('Program publication differs from its exact pending journal prefix')
            for event, record in zip(base.events, pending, strict=True):
                if any(event.get(key) != getattr(record, key) for key in (
                        'run_id', 'sequence', 'category', 'entity_type', 'entity_id', 'account_id')):
                    raise ValueError('Program normalized event differs from its staged source envelope')
                for key in ('event_time', 'recorded_at'):
                    at = event.get(key)
                    if isinstance(at, str):
                        at = datetime.fromisoformat(at.replace('Z', '+00:00'))
                    if not isinstance(at, datetime) or at.tzinfo is None or at != getattr(record, key):
                        raise ValueError('Program normalized event changed its actual source clock')
            intent_sources, oms_sources = self._prepare_committed_sources(base, pending)
            try:
                future = getattr(self._writer, submissions[type(unit)])(unit)
                wait = asyncio.ensure_future(asyncio.wrap_future(future))
                cancelled = None
                while True:
                    try:
                        committed = await asyncio.shield(wait)
                        break
                    except asyncio.CancelledError as exc:
                        if wait.cancelled():
                            raise RuntimeError('Native program commit receipt was cancelled') from exc
                        cancelled = exc
                        continue
                self.require_current()
                if str(UUID(str(committed))) != base.batch_id:
                    raise RuntimeError('Native program writer acknowledged another batch identity')
                self._program_intent_sources.update(intent_sources)
                self._program_oms_sources.update(oms_sources)
                for record in pending:
                    self._program_intent_inputs.pop(record.record_id, None)
                    self._program_modify_inputs.pop(record.record_id, None)
                self._buffer.mark_fenced(base.last_sequence)
                self._sequence, self._batch_id = base.last_sequence, base.batch_id
                self._source_cursor = base.source_cursor
                receipt = ProgramJournalReceipt(self.run_id, self._sequence, self._batch_id, self._source_cursor)
                if cancelled is not None:
                    self._failure = cancelled
                    raise cancelled
                return receipt
            except BaseException as exc:
                # A native submit can fail after accepting work; retain every
                # pending record and stop admission until cold reconciliation.
                self._failure = exc
                raise

    def _prepare_committed_sources(self, base, records):
        """Prepare bounded sidecars before I/O; promote only after its receipt."""
        from src.trading_runtime.arte_intent_projection import strategy_intent_batch
        from src.trading_runtime.arte_journal_writer import _sealed_families
        from .backtest_typed_projection import committed_oms_order_lineage
        families = None
        intents, oms = {}, {}
        for record in records:
            if (record.category, record.entity_type) == ('strategy', 'strategy_intent'):
                if families is None:
                    families = dict(_sealed_families(base))
                original = self._program_intent_inputs.get(record.record_id)
                if original is None:
                    raise ValueError('Program semantic publication lost its original typed intent')
                intent, assignment = original
                expected = strategy_intent_batch(intent, run_id=self.run_id, run_month=base.run_month,
                    account_id=record.account_id, attempt_id=base.attempt_id, batch_id=base.batch_id,
                    prior_batch_id=base.prior_batch_id, sequence=record.sequence,
                    source_cursor=base.source_cursor, run_status='running', recorded_at=record.recorded_at,
                    record_id=record.record_id,
                    correlation_id=str(record.payload.get('correlation_id') or ''),
                    causation_id=str(record.payload.get('causation_id') or ''))
                expected_row, = dict(_sealed_families(expected))['trading_strategy_intent_v1']
                actual = tuple(row for row in families.get('trading_strategy_intent_v1', ())
                               if row['record_id'] == record.record_id)
                if actual != (expected_row,):
                    raise ValueError('Program published semantic intent differs from its actual typed source')
                intents[(intent.intent_id, record.account_id)] = ProgramIntentSource(intent, assignment,
                    record.record_id, base.batch_id, expected_row['content_hash'], expected)
            elif (record.category, record.entity_type) == ('order_management', 'order_group_state'):
                group = self._buffer.oms_group_for_record(record.record_id)
                if group is None:
                    raise ValueError('Program OMS publication lost its original frozen group')
                lineage = committed_oms_order_lineage(group, run_id=self.run_id,
                    strategy_id=self._lease.registration.strategy_id,
                    strategy_revision=self._lease.registration.revision,
                    authorized_protection=self._buffer.oms_effective_protection_for_record(record))
                oms[(group.group_id, group.account_id)] = ProgramOmsSource(record.record_id,
                    base.batch_id, MappingProxyType(dict(lineage)))
        if (len(set(self._program_intent_sources) | set(intents)) > self.max_pending_records
                or len(set(self._program_oms_sources) | set(oms)) > self.max_pending_records):
            raise ValueError('Program committed source retention exceeds its explicit object bound')
        return intents, oms

    def flush(self):
        self.require_current()
        if self.pending_record_count:
            raise RuntimeError('Pending program facts require an explicit native receipt commit')

    def close(self):
        self.flush()
        self._closed = True
        self._buffer.close()

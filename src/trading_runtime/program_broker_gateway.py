"""Concrete programme broker boundary: native typed receipt before mutation.

Read operations delegate to the selected native broker. Mutations require
their original typed source unit and exact request; a bare OMS call cannot
accidentally bypass the journal lane.
"""
from copy import deepcopy
from dataclasses import asdict


class ProgramBrokerGateway:
    _READS = frozenset({'initialize', 'accounts', 'account_summary', 'account_ledger',
        'positions', 'live_orders', 'trades', 'on_market_event', 'shortability',
        'configure_session', 'suppress_messages', 'stream_broker_messages',
        'market_snapshot', 'close', 'config', 'requires_fresh_execution_state'})
    _REPLAY_READS = frozenset({'on_liquidity_bar', 'validate_liquidity_bar',
        '_on_validated_liquidity_bar', 'completed_liquidity_quote'})

    def __init__(self, broker, journal):
        from src.backend.program_execution_journal import ProgramExecutionJournal
        from .simulated_broker import SimulatedBrokerAdapter
        from .ibkr_client import IbkrClientPortalAdapter
        if (type(journal) is not ProgramExecutionJournal
                or type(broker) not in {SimulatedBrokerAdapter, IbkrClientPortalAdapter}
                or journal.mode == 'replay' and type(broker) is not SimulatedBrokerAdapter
                or journal.mode in {'paper', 'live'} and type(broker) is not IbkrClientPortalAdapter
                or getattr(getattr(broker, 'mode', None), 'value', None) != journal.mode):
            raise TypeError('Program broker requires its actual mode-native broker and owned journal')
        journal.require_current()
        self.broker, self.journal = broker, journal
        self._prepared = None
        self._failed = None
        self._assembly = None
        self._available = []
        self._staged_plans = {}

    def bind_assembly(self, assembly):
        from src.backend.program_runtime_assembly import ProgramRuntimeAssembly
        if type(assembly) is not ProgramRuntimeAssembly or assembly.broker is not self or self._assembly is not None:
            raise TypeError('Program broker binds its one concrete native assembly')
        self._assembly = assembly

    def _retain_receipts(self, publications):
        """Internal handoff from the concrete publisher, never caller evidence."""
        from .arte_journal_writer import TypedJournalBatch, V4OrderCancelBatch, V4OrderModifyCommandBatch
        from .arte_order_exact_prices import ExactPriceOrderCommandBatch
        from .arte_broker_policy_v4 import V4BrokerPolicyBatch
        for unit, receipt in publications:
            base = unit if type(unit) is TypedJournalBatch else unit.base
            if (receipt.batch_id != base.batch_id or receipt.last_sequence != base.last_sequence
                    or receipt.run_id != self.journal.run_id or receipt.last_sequence > self.journal.fenced_sequence):
                raise RuntimeError('Program broker received foreign native journal receipt')
            if type(unit) in {V4OrderCancelBatch, V4OrderModifyCommandBatch, ExactPriceOrderCommandBatch, V4BrokerPolicyBatch} or (
                    type(unit) is TypedJournalBatch and unit.order_commands):
                self._available.append(unit)
                if len(self._available) > self.journal.max_pending_records:
                    self._failed = RuntimeError('Unconsumed broker source facts exceeded the owned staging bound')
                    raise self._failed

    async def stage_managed_repair_plan(self, group, batches):
        """Persist the actual new native repair plan before any broker request."""
        from copy import copy
        from dataclasses import replace
        from .order_management import _ManagedOrderGroup
        from .ibkr_schema import OrderRequest
        if self._assembly is None or type(group) is not _ManagedOrderGroup:
            raise TypeError('Program repair requires its actual native managed group')
        runtime = self._assembly.runtime
        self._assembly.require_runtime(runtime)
        batches = tuple(tuple(batch) for batch in batches)
        requests = tuple(request for batch in batches for request in batch)
        if (runtime.order_manager._groups.get(group.group_id) is not group or not 1 <= len(requests) <= 4096
                or any(not batch for batch in batches)
                or any(type(r) is not OrderRequest or r.acctId != group.account_id or not r.cOID for r in requests)
                or len({r.cOID for r in requests}) != len(requests)
                or {r.cOID for r in requests} & {r.cOID for r in group.orders}):
            raise ValueError('Program repair plan differs from actual new native requests')
        async with self._assembly.operation():
            candidate = copy(group)
            candidate.orders = [*group.orders, *requests]
            candidate.plan = replace(group.plan, orders=tuple(candidate.orders),
                batches=(*group.plan.broker_batches, *batches),
                order_slice_ids=(*(group.plan.order_slice_ids or tuple('' for _ in group.orders)),
                                 *('repair-protection' for _ in requests)))
            runtime.order_manager._record('order_management', 'order_group_state', group.group_id,
                group.account_id, candidate.updated_at,
                {'event': 'program_managed_repair_plan_staged', 'order_group_id': group.group_id},
                group_snapshot=candidate)
            for request in requests:
                runtime.order_manager._record('command', 'order', request.cOID, group.account_id,
                    candidate.updated_at, {**request.to_cpapi(), 'strategy_intent_id': group.intent.intent_id,
                        'order_group_id': group.group_id, 'policy_version': runtime.order_manager.policy.version},
                    order_request=request)
            await self._assembly.flush()
            for request in requests:
                if len(self._staged_plans) >= self.journal.max_pending_records:
                    raise RuntimeError('Unconsumed managed repair plans exceeded bounded custody')
                self._staged_plans[request.cOID] = (group, deepcopy(request))

    async def _prepare_native(self, operation, account_id, order_id, orders):
        """Capture repair mutations emitted by OMS before its legacy post-record."""
        from .arte_journal_writer import TypedJournalBatch, V4OrderCancelBatch, V4OrderModifyCommandBatch
        from .arte_order_exact_prices import ExactPriceOrderCommandBatch
        from .arte_command_dispatcher import validate_program_command_batch, _command_matches_order
        from dataclasses import replace
        if self._assembly is None:
            raise RuntimeError('Program broker lacks its concrete OMS assembly')
        runtime = self._assembly.runtime
        groups = [g for g in runtime.order_manager._groups.values() if g.account_id == account_id and (
            order_id in g.broker_order_ids if order_id else any(r.cOID in {v.cOID for v in g.orders} for r in orders))]
        if operation == 'place' and not groups:
            retained = [self._staged_plans.get(request.cOID) for request in orders]
            if (retained and all(value is not None for value in retained)
                    and len({id(value[0]) for value in retained}) == 1
                    and all(asdict(value[1]) == asdict(request) for value, request in zip(retained, orders))):
                groups = [retained[0][0]]
        if len(groups) != 1:
            raise RuntimeError('Broker mutation requires one exact native OMS group')
        group = groups[0]
        source = self.journal.program_intent_source(group.intent.intent_id, account_id)
        if source is None:
            raise RuntimeError('Broker mutation lacks committed original semantic intent')
        common = dict(strategy_id=runtime.config.strategy_id, strategy_revision=runtime.config.strategy_revision,
            correlation_id=runtime.run_id, causation_id=group.intent.intent_id,
            order_group_id=group.group_id, intent_id=group.intent.intent_id,
            reason='program_native_oms_' + operation)
        if operation == 'cancel':
            existing = [r for r in self.journal.unfenced_records(after_sequence=self.journal.fenced_sequence)
                if (r.category, r.entity_type, r.entity_id, r.account_id) ==
                ('command', 'order_cancel', order_id, account_id)]
            if not existing:
                self.journal.append(run_id=runtime.run_id, category='command', entity_type='order_cancel',
                    entity_id=order_id, account_id=account_id, event_time=runtime.last_event_time,
                    payload={**common, 'ticker': group.intent.ticker})
        elif operation == 'modify':
            oms = self.journal.program_oms_source(group.group_id, account_id)
            if oms is None:
                raise RuntimeError('Broker modification lacks committed native OMS revision')
            self.journal.append_program_order_modify(request=replace(orders[0], raw={}),
                run_id=runtime.run_id, category='command', entity_type='order_modify', entity_id=order_id,
                account_id=account_id, event_time=runtime.last_event_time,
                payload={**common, 'source_intent_record_id': source.record_id,
                    'source_intent_content_hash': source.content_hash, 'oms_group_record_id': oms.record_id})
        await self._assembly.flush()
        registration = self.journal.writer._live_v4_lease.registration
        chosen = []
        if operation == 'place':
            for request in orders:
                found = []
                for unit in self._available:
                    if type(unit) not in {TypedJournalBatch, ExactPriceOrderCommandBatch}:
                        continue
                    base = unit if type(unit) is TypedJournalBatch else unit.base
                    try:
                        validate_program_command_batch(unit, account_id, (request,), registration=registration)
                    except ValueError:
                        continue
                    found.append(unit)
                if len(found) != 1:
                    raise RuntimeError('Broker placement lacks one exact committed request per leg')
                chosen.append(found[0])
        else:
            for unit in self._available:
                detail = (unit.cancellation if type(unit) is V4OrderCancelBatch else
                          unit.modification if type(unit) is V4OrderModifyCommandBatch else None)
                if (detail is None
                        or detail['broker_order_id'] != order_id
                        or unit.base.events[0]['account_id'] != account_id):
                    continue
                if operation == 'cancel' and type(unit) is V4OrderCancelBatch or (
                        operation == 'modify' and type(unit) is V4OrderModifyCommandBatch
                        and _command_matches_order(detail, orders[0])):
                    chosen.append(unit)
            if len(chosen) != 1:
                raise RuntimeError('Broker amendment lacks one exact committed native source')
        for unit in chosen:
            self._available.remove(unit)
        for request in orders:
            self._staged_plans.pop(request.cOID, None)
        self._prepared = (operation, account_id, order_id, tuple(deepcopy(orders)), None)

    def __getattr__(self, name):
        if name not in self._READS and not (self.journal.mode == 'replay' and name in self._REPLAY_READS):
            raise AttributeError(name)
        return getattr(self.broker, name)

    async def prepare(self, unit, *, operation, account_id, orders=(), order_id='', confirmed=None):
        """Seal exactly one native broker call; consume it once after receipt.

        The surrounding native assembly must own its operation lock. A second
        prepared mutation or uncertain broker acknowledgement requires recovery.
        """
        from .arte_journal_writer import TypedJournalBatch, V4OrderCancelBatch, V4OrderModifyCommandBatch
        from .arte_order_exact_prices import ExactPriceOrderCommandBatch
        from .arte_command_dispatcher import validate_program_command_batch, _command_matches_order
        if self._failed is not None or self._prepared is not None:
            raise RuntimeError('Program broker requires cold command reconciliation') from self._failed
        self.journal.require_current()
        registration = self.journal.writer._live_v4_lease.registration
        if operation == 'place' and type(unit) in {TypedJournalBatch, ExactPriceOrderCommandBatch}:
            validate_program_command_batch(unit, account_id, tuple(orders), registration=registration)
        elif operation == 'cancel' and type(unit) is V4OrderCancelBatch:
            detail = unit.cancellation
            if (detail['result_kind'] != 'command' or detail['broker_order_id'] != order_id
                    or not detail['order_group_id'] or not detail['intent_id']
                    or unit.base.events[0]['account_id'] != account_id
                    or (detail['strategy_id'], detail['strategy_revision']) != registration.key):
                raise ValueError('Program cancellation differs from exact native source')
        elif operation == 'modify' and type(unit) is V4OrderModifyCommandBatch and len(orders) == 1:
            if (unit.modification['broker_order_id'] != order_id
                    or not _command_matches_order(unit.modification, orders[0])
                    or unit.base.events[0]['account_id'] != account_id
                    or (unit.modification['strategy_id'], unit.modification['strategy_revision']) != registration.key):
                raise ValueError('Program modification differs from exact native source')
        else:
            raise ValueError('Program broker operation has no exact supported typed recipe')
        sealed = (operation, account_id, order_id, tuple(deepcopy(orders)), confirmed)
        await self.journal.commit_native(unit)
        self.journal.require_current()
        self._prepared = sealed

    async def _dispatch(self, operation, account_id, order_id='', orders=(), confirmed=None):
        if self._failed is not None:
            raise RuntimeError('Program broker outcome requires cold reconciliation') from self._failed
        if self._assembly is None:
            raise RuntimeError('Broker mutation requires its context-bound native runtime assembly')
        self._assembly.require_runtime(self._assembly.runtime)
        if self._prepared is None and self._assembly is not None:
            await self._prepare_native(operation, account_id, order_id, orders)
        expected = self._prepared
        actual = (operation, account_id, order_id, tuple(orders), confirmed)
        if (expected is None or actual[:3] != expected[:3] or actual[4] != expected[4]
                or [asdict(v) for v in actual[3]] != [asdict(v) for v in expected[3]]):
            raise RuntimeError('Broker mutation lacks its exact committed native command')
        self._prepared = None  # Never retry an uncertain broker call.
        self.journal.require_current()
        try:
            if operation == 'place':
                return await self.broker.place_orders(account_id, list(expected[3]))
            if operation == 'cancel':
                return await self.broker.cancel_order(account_id, order_id)
            return await self.broker.modify_order(account_id, order_id, expected[3][0])
        except BaseException as exc:
            self._failed = exc
            raise

    async def place_orders(self, account_id, orders):
        return await self._dispatch('place', account_id, orders=orders)

    async def cancel_order(self, account_id, order_id):
        return await self._dispatch('cancel', account_id, order_id)

    async def modify_order(self, account_id, order_id, order):
        return await self._dispatch('modify', account_id, order_id, (order,))

    async def reply(self, reply_id, confirmed):
        from .arte_broker_policy_v4 import V4BrokerPolicyBatch
        if self._assembly is None or type(confirmed) is not bool or self._failed is not None:
            raise RuntimeError('Broker warning reply requires a normalized policy/receipt recipe')
        self._assembly.require_runtime(self._assembly.runtime)
        await self._assembly.flush()
        matches = [unit for unit in self._available if type(unit) is V4BrokerPolicyBatch
            and unit.base.events[0]['entity_id'] == reply_id
            and unit.base.events[0]['entity_type'] == 'order_warning_decision'
            and unit.policy['confirmed'] == int(confirmed)]
        if len(matches) != 1:
            raise RuntimeError('Broker reply differs from one exact committed native policy decision')
        self._available.remove(matches[0])
        self.journal.require_current()
        try:
            return await self.broker.reply(reply_id, confirmed)
        except BaseException as exc:
            self._failed = exc
            raise

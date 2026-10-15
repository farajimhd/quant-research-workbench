"""Bounded actual broker executions, committed before programme projection."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.ibkr_schema import Execution


@dataclass(frozen=True, slots=True)
class ProgramExecutionIntakeResult:
    observed: int
    committed_fills: int
    committed_commissions: int
    deferred: int
    excluded_accounts: tuple


class ProgramBrokerExecutionIntake:
    def __init__(self, assembly, *, maximum_executions=65536):
        from .program_runtime_assembly import ProgramRuntimeAssembly
        if type(assembly) is not ProgramRuntimeAssembly or type(maximum_executions) is not int or not 1 <= maximum_executions <= 65536:
            raise TypeError('Execution intake requires concrete native assembly and bounded custody')
        self.assembly, self.maximum_executions = assembly, maximum_executions
        self._seen, self._deferred, self._failed = {}, {}, None
        self._last_at = None

    def require_current(self):
        if self._failed is not None:
            raise RuntimeError('Program execution intake requires cold receipt recovery') from self._failed

    @property
    def deferred_count(self):
        return len(self._deferred)

    async def poll(self, *, at):
        """Read native IBKR facts; never reconstruct fills from QMD prices."""
        self.require_current()
        if self.assembly.journal.mode not in {'paper', 'live'}:
            raise ValueError('Actual IBKR execution polling requires its native Paper/Live assembly')
        try:
            rows = tuple(await self.assembly.broker.trades(days=1))
            return await self._ingest(rows, at=at)
        except BaseException as exc:
            self._failed = exc
            raise

    async def observe_replay(self, rows, *, at):
        self.require_current()
        broker = self.assembly.broker.broker
        if (self.assembly.journal.mode != 'replay'
                or any(not any(row is actual for actual in broker._executions) for row in rows)):
            raise ValueError('Replay execution intake requires actual native broker fill objects')
        try:
            return await self._ingest(tuple(rows), at=at)
        except BaseException as exc:
            self._failed = exc
            raise

    async def _ingest(self, rows, *, at):
        self.require_current()
        runtime, journal = self.assembly.runtime, self.assembly.journal
        if (not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None
                or self._last_at is not None and at < self._last_at
                or len(rows) > 4096 or any(type(row) is not Execution for row in rows)):
            raise ValueError('Broker execution intake lacks bounded typed facts and completed clock')
        excluded = {}
        candidates = dict(self._deferred)
        for row in rows:
            if row.account not in runtime.config.account_ids:
                excluded[row.account] = excluded.get(row.account, 0) + 1
                continue
            prior = candidates.get(row.execution_id)
            if prior is not None and replace(prior, commission=None) != replace(row, commission=None):
                raise ValueError('Deferred broker execution identity changed its actual fill')
            candidates[row.execution_id] = row
        if len(candidates) > self.maximum_executions:
            raise RuntimeError('Deferred actual execution facts exceeded bounded custody')
        staged_seen = dict(self._seen)
        deferred, owned, entries = {}, [], []
        fills = fees = 0
        for identity, row in sorted(candidates.items(), key=lambda item: (item[1].trade_time, item[0])):
            if row.trade_time.tzinfo is None:
                raise ValueError('Actual broker execution lacks an aware exchange clock')
            if row.trade_time > at:
                deferred[identity] = row
                continue
            payload = replace(row, commission=None).to_cpapi()
            payload.pop('commission', None)
            fingerprint = sha256(canonical_json(payload).encode()).hexdigest()
            previous = staged_seen.get(identity)
            if previous is not None and previous[0] != fingerprint:
                raise ValueError('Actual broker execution ID changed its committed fill content')
            new_fill = previous is None
            previous_fee = previous[1] if previous is not None else None
            if previous_fee is not None and row.commission is not None and row.commission != previous_fee:
                raise ValueError('Actual broker commission changed its committed final value')
            new_fee = row.commission is not None and previous_fee is None
            if not new_fill and not new_fee:
                continue
            if new_fill:
                entries.append(dict(run_id=runtime.run_id, category='execution', entity_type='fill',
                    entity_id=identity, account_id=row.account, event_time=row.trade_time, payload=payload))
                fills += 1
            if new_fee:
                entries.append(dict(run_id=runtime.run_id, category='execution', entity_type='commission',
                    entity_id=identity, account_id=row.account, event_time=row.trade_time,
                    payload=dict(execution_id=identity, commission=row.commission, currency=row.currency,
                                 status='final', time_authority='execution')))
                fees += 1
            manager = runtime.order_manager
            if row.order_id in manager._group_by_broker_id or row.order_ref in manager._group_by_client_id:
                manager.execution_attribution(row)  # Exact known lineage must validate.
                owned.append(row)
            staged_seen[identity] = (fingerprint, row.commission if row.commission is not None else previous_fee)
        if len(staged_seen) > self.maximum_executions:
            raise RuntimeError('Committed broker execution deduplication exceeded bounded custody')
        try:
            if entries:
                journal.append_many(entries)
                await self.assembly.flush()
                # A receipt creates authority for the actual fill, never a
                # programme proposal, position estimate or broker order ack.
                runtime.strategy.on_broker_executions(tuple(owned), runtime.order_manager)
            self._seen, self._deferred = staged_seen, deferred
            self._last_at = at
        except BaseException as exc:
            self._failed = exc
            raise
        return ProgramExecutionIntakeResult(len(rows), fills, fees, len(deferred), tuple(sorted(excluded.items())))

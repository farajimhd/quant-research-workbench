"""Receipt-gated native Portfolio state transitions, without SQLite writes.

This is a staged current-path primitive. It does not install a runtime, issue
orders, select a journal principal or bypass production admission guards.
Existing normalized snapshot families retain the complete resulting state.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from dataclasses import dataclass
from hashlib import sha256

from .arte_journal_writer import ArteJournalWriter
from .arte_portfolio_snapshot import load_portfolio_snapshot
from .journal_contract import canonical_json
from .keeper_ownership import KeeperOwnershipCoordinator
from .portfolio import PortfolioManagementEngine, PortfolioSyncState


_ROOT = '/trading/portfolio-current-head/v1'
_METHODS = {
    'approve_intent': {'intent', 'assignment_id'},
    'release_intent': {'intent_id', 'reason'},
    'order_group_update': {'snapshot'},
    'set_control': {'mode', 'reason'},
    'set_allocation_enabled': {'strategy_id', 'enabled', 'reason'},
    'select_policy': {'policy', 'reason'},
}


def _path(run_id, account_id):
    if any(type(v) is not str or not 1 <= len(v) <= 256 for v in (run_id, account_id)):
        raise ValueError('Portfolio transition requires bounded exact run/account identities')
    return _ROOT + '/' + sha256(account_id.encode()).hexdigest()


def _wire(phase, revision, transition_hash, snapshot_hash, source_run_id):
    import re
    if (phase not in {'committed', 'pending'} or type(revision) is not int or revision < 1
            or any(type(v) is not str or re.fullmatch('[0-9a-f]{64}', v) is None
                   for v in (transition_hash, snapshot_hash))):
        raise ValueError('Invalid Portfolio transition head')
    if type(source_run_id) is not str or not 1 <= len(source_run_id) <= 256 or any(c in source_run_id for c in '\r\n\0'):
        raise ValueError('Shared Portfolio head requires exact source run identity')
    return f'1\n{phase}\n{revision}\n{transition_hash}\n{snapshot_hash}\n{source_run_id}'.encode()


def _read_head(client, path):
    raw, stat = client.get(path)
    try:
        version, phase, revision, transition_hash, snapshot_hash, source_run_id = raw.decode().split('\n')
        if version != '1' or _wire(phase, int(revision), transition_hash, snapshot_hash, source_run_id) != raw:
            raise ValueError('invalid wire')
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('Corrupt Portfolio transition head') from exc
    return phase, int(revision), transition_hash, snapshot_hash, stat.version, source_run_id


@dataclass(frozen=True, slots=True)
class ProgramPortfolioTransitionReceipt:
    run_id: str
    account_id: str
    state_revision: int
    transition_hash: str
    snapshot_hash: str
    snapshot_at: object = None


@dataclass(frozen=True, slots=True)
class ProgramPortfolioAdmissionResult:
    receipt: ProgramPortfolioTransitionReceipt
    decision: object
    approved_intent: object


class ProgramPortfolioTransitionAuthority:
    """Existing writer + Keeper epoch fence + exact normalized snapshot readback."""
    def __init__(self, *, client, writer, coordinator, attempt_id):
        if type(writer) is not ArteJournalWriter or type(coordinator) is not KeeperOwnershipCoordinator:
            raise TypeError('Portfolio transitions require native writer and Keeper coordinator')
        self.client, self.writer, self.coordinator = client, writer, coordinator
        from .program_portfolio_journal import ProgramPortfolioRecordJournal
        self.record_journal = ProgramPortfolioRecordJournal(client=client, writer=writer, attempt_id=attempt_id)
        self._global_claims = {}
        self._admission_sources = {}

    async def consume_admission_source(self, engine, outcome):
        """One-use custody of original source facts after exact head readback."""
        retained = self._admission_sources.get(id(outcome))
        if retained is None or retained[0] is not outcome:
            raise RuntimeError('Program admission has no native publication custody')
        head = await self.verify_current_head(engine, outcome.receipt.account_id)
        if head != outcome.receipt:
            raise RuntimeError('Program admission no longer owns the exact shared Portfolio head')
        del self._admission_sources[id(outcome)]
        return deepcopy(retained[1])

    @asynccontextmanager
    async def claim(self, engine, account_id, group_ids):
        from uuid import uuid4
        owner = f'{engine.run_id}:{uuid4()}'
        claims = []
        try:
            for resource in (*(f'portfolio-group:{key}' for key in group_ids), f'portfolio-account:{account_id}'):
                claim = await asyncio.to_thread(self.coordinator.acquire_portfolio_admission_lease,
                    resource, owner_id=owner, ttl_seconds=30.)
                if claim is None:
                    raise RuntimeError('Shared Portfolio account/group is owned by another process')
                claims.append(claim)
            async with self.coordinator.claim_portfolio_snapshot(engine.run_id, account_id) as lease:
                self._global_claims[id(lease)] = tuple(claims)
                try:
                    yield lease
                finally:
                    self._global_claims.pop(id(lease), None)
        finally:
            for claim in reversed(claims):
                await asyncio.to_thread(self.coordinator.release_portfolio_admission_lease,
                    claim['resource_id'], owner_id=claim['owner_id'], epoch=claim['epoch'])

    def initialize_from_recovered_snapshot(self, *, run_id, account_id, revision, snapshot_hash):
        """Explicit cold provisioning only; existing heads require reconciliation."""
        loaded = load_portfolio_snapshot(self.client, run_id=run_id, account_id=account_id,
                                         state_revision=revision)
        if loaded is None or loaded['state_hash'] != snapshot_hash:
            raise ValueError('Portfolio transition initialization lacks exact committed snapshot')
        client = self.coordinator._client
        client.ensure_path(_ROOT)
        client.create(_path(run_id, account_id), _wire('committed', revision, '0' * 64, snapshot_hash, run_id))

    async def publish(self, captured, *, records, prior_revision, prior_hash, transition_hash, lease):
        coordinator, keeper = self.coordinator, self.coordinator._client
        path = _path(captured.run_id, captured.account_id)
        head = await asyncio.to_thread(_read_head, keeper, path)
        if (head[0] != 'committed' or head[1] != prior_revision or head[3] != prior_hash
                or captured.state_revision != prior_revision + 1
                or not await asyncio.to_thread(coordinator.portfolio_snapshot_claim_is_current, lease)):
            raise RuntimeError('Portfolio transition lacks its exact recovered head and claim')
        # A pending head survives crashes and ambiguous acknowledgements. Never
        # infer absence of a delayed INSERT from a negative row read.
        await asyncio.to_thread(keeper.set, path,
            _wire('pending', captured.state_revision, transition_hash, prior_hash, head[5]), version=head[4])
        source_receipt = await self.record_journal.publish(records, captured, transition_hash=transition_hash)
        digest = await asyncio.wrap_future(self.writer.submit_captured_portfolio_snapshot(captured))
        loaded = await asyncio.to_thread(load_portfolio_snapshot, self.client,
            run_id=captured.run_id, account_id=captured.account_id, state_revision=captured.state_revision)
        if loaded is None or loaded['state_hash'] != digest:
            raise RuntimeError('Portfolio transition snapshot receipt differs from durable readback')
        batch_id = source_receipt.batch_id
        # Existing coordinator atomically checks ephemeral holder+epoch and
        # creates the immutable snapshot receipt in the same Keeper transaction.
        await asyncio.to_thread(coordinator.attest_portfolio_snapshot_receipt, lease,
            captured.run_id, captured.account_id, captured.state_revision, batch_id, digest,
            additional_leases=self._global_claims[id(lease)])
        pending = await asyncio.to_thread(_read_head, keeper, path)
        if pending[:4] != ('pending', captured.state_revision, transition_hash, prior_hash):
            raise RuntimeError('Portfolio transition head changed during snapshot publication')
        await asyncio.to_thread(keeper.set, path,
            _wire('committed', captured.state_revision, transition_hash, digest, captured.run_id), version=pending[4])
        final = await asyncio.to_thread(_read_head, keeper, path)
        if (final[:4] != ('committed', captured.state_revision, transition_hash, digest)
                or not await asyncio.to_thread(coordinator.portfolio_snapshot_claim_is_current, lease)):
            raise RuntimeError('Portfolio transition lost its final receipt/owner fence')
        return ProgramPortfolioTransitionReceipt(captured.run_id, captured.account_id,
            captured.state_revision, transition_hash, digest, captured.snapshot_at)

    async def require_current_image(self, engine, account_id, prior_revision, prior_hash, *, source_run_id=None,
                                    allow_recovery_blocked=False):
        from datetime import datetime
        from .arte_portfolio_snapshot import prepare_captured_portfolio_snapshot, decode_prepared_portfolio_snapshot
        loaded = await asyncio.to_thread(load_portfolio_snapshot, self.client,
            run_id=source_run_id or engine.run_id, account_id=account_id, state_revision=prior_revision)
        if loaded is None or loaded['state_hash'] != prior_hash:
            raise RuntimeError('Portfolio transition has no exact committed prior image')
        image = engine
        if allow_recovery_blocked:
            # Recovery deliberately invalidates broker freshness. Compare its
            # durable capital image without installing the persisted freshness.
            image = copy(engine)
            image.states = dict(engine.states)
            state = deepcopy(engine.states[account_id])
            state.sync_state = PortfolioSyncState(loaded['state']['sync_state'])
            state.stale_reason = loaded['state']['stale_reason']
            image.states[account_id] = state
        captured = image.capture_recovery_snapshot(account_id, state_revision=prior_revision,
            snapshot_at=datetime.fromisoformat(loaded['snapshot_at']))
        current = await asyncio.to_thread(prepare_captured_portfolio_snapshot, captured)
        if decode_prepared_portfolio_snapshot(current) != loaded['state']:
            raise RuntimeError('Portfolio memory differs from committed prior image; synchronize or recover first')
        return loaded

    async def verify_current_head(self, engine, account_id, *, as_of=None):
        """Return only a current image backed by source commit and epoch proof."""
        from .arte_journal_commit_v4 import load_verified_v4_prefix
        head = await asyncio.to_thread(_read_head, self.coordinator._client, _path(engine.run_id, account_id))
        if head[0] != 'committed' or engine._typed_admission_poisoned:
            raise RuntimeError('Portfolio head is pending or poisoned')
        loaded = await self.require_current_image(engine, account_id, head[1], head[3], source_run_id=head[5])
        from datetime import datetime
        snapshot_at = datetime.fromisoformat(loaded['snapshot_at'])
        if as_of is not None and (not isinstance(as_of, datetime) or as_of.tzinfo is None
                or as_of.utcoffset() is None or snapshot_at > as_of):
            raise RuntimeError('Committed Portfolio head is later than the completed decision boundary')
        proof = await asyncio.to_thread(self.coordinator.load_portfolio_snapshot_receipt,
            head[5], account_id, head[1])
        prefix = await asyncio.to_thread(load_verified_v4_prefix, self.client, head[5])
        if (proof is None or proof.snapshot_hash != head[3] or prefix is None
                or proof.batch_id not in prefix.batch_ids):
            raise RuntimeError('Portfolio head lacks exact committed source and Keeper epoch receipt')
        if await asyncio.to_thread(_read_head, self.coordinator._client,
                _path(engine.run_id, account_id)) != head:
            raise RuntimeError('Portfolio head moved while qualifying the current image')
        return ProgramPortfolioTransitionReceipt(head[5], account_id, head[1], head[2], head[3], snapshot_at)

    async def synchronize_broker_snapshot(self, engine, *, account_id, summary, ledger, positions, open_orders):
        """Commit real broker reconciliation under the shared account/group head.

        Uses existing normalized reconciliation and snapshot families in V4.
        Legacy V1/V3 synchronization publication remains separate.
        """
        from .arte_portfolio_sync import TypedPortfolioSyncAuthority, PortfolioSyncReceipt
        if (type(engine) is not PortfolioManagementEngine or not engine._typed_recovery
                or engine._typed_backtest_recovery or engine._typed_admission_poisoned):
            raise ValueError('Program broker sync requires native blocked/recovered Portfolio')
        owner = self
        groups = sorted(g.group_id for g in engine.groups.values()
                        if engine.states[account_id].profile.account_key in g.account_keys)
        class SharedSync(TypedPortfolioSyncAuthority):
            def __init__(self):
                self.head = None
            @asynccontextmanager
            async def claim(self, run_id, selected_account):
                if (run_id, selected_account) != (engine.run_id, account_id):
                    raise ValueError('Shared sync account/run differs')
                async with owner.claim(engine, account_id, groups) as lease:
                    self.head = await asyncio.to_thread(_read_head, owner.coordinator._client,
                        _path(run_id, account_id))
                    if self.head[0] != 'committed':
                        raise RuntimeError('Shared Portfolio head needs cold reconciliation')
                    await owner.require_current_image(engine, account_id, self.head[1], self.head[3],
                        source_run_id=self.head[5], allow_recovery_blocked=True)
                    yield lease
            def claim_is_current(self, lease):
                return owner.coordinator.portfolio_snapshot_claim_is_current(lease)
            def next_revision(self, run_id, selected_account, lease):
                if self.head is None:
                    raise RuntimeError('Shared sync has no pinned capital head')
                return self.head[1] + 1
            async def publish(self, records, captured, lease):
                digest = sha256(canonical_json({'transition': 'broker_reconciliation', 'records': records,
                    'prior_revision': self.head[1], 'prior_hash': self.head[3]}).encode()).hexdigest()
                receipt = await owner.publish(captured, records=records, prior_revision=self.head[1],
                    prior_hash=self.head[3], transition_hash=digest, lease=lease)
                return PortfolioSyncReceipt(receipt.run_id, receipt.account_id,
                    receipt.state_revision, receipt.snapshot_hash)
        locks = [engine._group_locks[key] for key in groups]
        acquired = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            await engine.synchronize_typed_snapshot(account_id, summary=summary, ledger=ledger,
                positions=positions, open_orders=open_orders, authority=SharedSync())
        finally:
            for lock in reversed(acquired):
                lock.release()


async def apply_program_portfolio_transition(engine, authority, *, account_id,
        prior_revision, prior_hash, transition, arguments):
    """Install only after snapshot+Keeper proof; failed publication poisons admission.

    Native methods run on detached candidate maps. They capture their ordinary
    records in memory while the existing normalized snapshot stores the full
    resulting reservations, allocations, controls and policy references.
    """
    if (type(engine) is not PortfolioManagementEngine
            or type(authority) is not ProgramPortfolioTransitionAuthority
            or transition not in _METHODS or type(arguments) is not dict
            or set(arguments) != _METHODS[transition] or not engine._typed_recovery
            or engine._typed_backtest_recovery or engine._typed_admission_poisoned):
        raise ValueError('Portfolio transition requires explicit native recovered authority and closed arguments')
    state = engine._state(account_id)
    group_ids = sorted(group.group_id for group in engine.groups.values()
                       if state.profile.account_key in group.account_keys)
    locks = [engine._group_locks[key] for key in group_ids] + [engine._account_locks[account_id]]
    acquired = []
    publication_attempted = False
    try:
        for lock in locks:
            await lock.acquire()
            acquired.append(lock)
        async with authority.claim(engine, account_id, group_ids) as lease:
            head = await asyncio.to_thread(_read_head, authority.coordinator._client, _path(engine.run_id, account_id))
            if head[0] != 'committed' or (head[1], head[3]) != (prior_revision, prior_hash):
                raise RuntimeError('Shared Portfolio head differs from reviewed prior image')
            await authority.require_current_image(engine, account_id, prior_revision, prior_hash, source_run_id=head[5])
            if transition == 'approve_intent':
                # A global group lease serializes owners but does not refresh
                # their memory. Every contributing account must still match
                # its actual committed shared head before capacity is computed.
                peer_keys = {key for group in engine.groups.values() if group.group_id in group_ids
                             for key in group.account_keys}
                for peer_key in sorted(peer_keys - {state.profile.account_key}):
                    peer = engine.by_key.get(peer_key)
                    if peer is None:
                        raise RuntimeError('Shared Portfolio group lacks a contributing account image')
                    await authority.verify_current_head(engine, peer.profile.account_id)
            candidate = copy(engine)
            for name in ('states', 'reservations', 'allocations', 'differences', '_last_filled_by_reservation',
                         'decisions', 'rebalance_proposals'):
                setattr(candidate, name, deepcopy(getattr(engine, name)))
            candidate.by_key = {value.profile.account_key: value for value in candidate.states.values()}
            candidate.control_plane = None  # Publish operational control only after proof.
            candidate._typed_admission_stage = []
            args = deepcopy(arguments)
            key = state.profile.account_key
            admission = None
            if transition == 'approve_intent':
                from dataclasses import replace
                from .signals import StrategyIntent
                if type(args['intent']) is not StrategyIntent or not args['assignment_id']:
                    raise ValueError('Admission needs its exact native intent and assignment')
                intent = args['intent']
                if intent.metadata.get('assignment_id') not in (None, args['assignment_id']):
                    raise ValueError('Admission assignment differs from source intent')
                intent = replace(intent, metadata={**intent.metadata, 'assignment_id': args['assignment_id']})
                candidate._active_admission_lease = authority._global_claims[id(lease)][-1]
                admission = candidate._approve_locked(intent, candidate.states[account_id])
            elif transition == 'release_intent':
                matches = [r for r in candidate.reservations.values() if r.intent_id == args['intent_id']]
                if any(r.account_id != account_id for r in matches):
                    raise ValueError('Release transition differs from its account')
                candidate.release_intent(**args)
            elif transition == 'order_group_update':
                if args['snapshot'].account_id != account_id:
                    raise ValueError('OMS transition differs from its account')
                candidate.on_order_group_update(**args)
            elif transition == 'set_control':
                candidate.set_control(key, **args)
            elif transition == 'set_allocation_enabled':
                if type(args['enabled']) is not bool:
                    raise ValueError('Allocation transition requires a typed boolean')
                candidate.set_strategy_allocation_enabled(key, **args)
            else:
                candidate.select_policy(key, **args)
            if not candidate._typed_admission_stage:
                raise ValueError('Portfolio transition produced no native state record')
            records = tuple(candidate._typed_admission_stage)
            if any(record[2] != account_id for record in records):
                raise ValueError('Portfolio transition contains foreign account facts')
            digest = sha256(canonical_json({'transition': transition, 'records': records,
                'prior_revision': prior_revision, 'prior_hash': prior_hash}).encode()).hexdigest()
            captured = candidate.capture_recovery_snapshot(account_id,
                state_revision=prior_revision + 1, snapshot_at=candidate._event_time())
            publication_attempted = True
            receipt = await authority.publish(captured, prior_revision=prior_revision,
                prior_hash=prior_hash, transition_hash=digest, lease=lease, records=records)
            # No await during installation; peer account state is preserved.
            installed = candidate.states[account_id]
            engine.states[account_id] = installed
            engine.by_key[installed.profile.account_key] = installed
            for name, account in (('reservations', lambda r: r.account_id),
                                  ('allocations', lambda r: r.account_id),
                                  ('differences', lambda r: r.account_key)):
                target, source = getattr(engine, name), getattr(candidate, name)
                owner = installed.profile.account_key if name == 'differences' else account_id
                for identity in [identity for identity, row in target.items() if account(row) == owner]:
                    del target[identity]
                target.update({identity: row for identity, row in source.items() if account(row) == owner})
            owned_reservations = {key for key, row in engine.reservations.items() if row.account_id == account_id}
            for key in owned_reservations:
                if key in candidate._last_filled_by_reservation:
                    engine._last_filled_by_reservation[key] = candidate._last_filled_by_reservation[key]
            if engine.control_plane is not None:
                engine.control_plane.account_control_modes[account_id] = installed.control_mode.value
            if admission is not None:
                # Preserve other accounts' decisions produced while durability
                # was pending. Install this account's new native decisions only.
                existing = {d.decision_id for d in engine.decisions}
                engine.decisions.extend(d for d in candidate.decisions
                    if d.account_id == account_id and d.decision_id not in existing)
                decision, approved = admission
                result = ProgramPortfolioAdmissionResult(receipt, decision, approved)
                if len(authority._admission_sources) >= 65536:
                    raise RuntimeError('Unconsumed native Portfolio admissions exceeded bounded custody')
                authority._admission_sources[id(result)] = (result, records)
                return result
            return receipt
    except BaseException:
        if publication_attempted:
            engine._typed_admission_poisoned = True
            engine.states[account_id].sync_state = PortfolioSyncState.ENTRIES_BLOCKED
            engine.states[account_id].stale_reason = 'Portfolio transition outcome uncertain; cold recovery required'
        raise
    finally:
        for lock in reversed(acquired):
            lock.release()

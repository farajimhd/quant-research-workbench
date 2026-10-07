"""Declared exclusive-writer reuse of immutable, completely verified proofs."""
from dataclasses import dataclass, field
from hashlib import sha256
from threading import RLock
from weakref import WeakKeyDictionary
from uuid import UUID

RULE = 'fixed-structural-lot-exclusive-writer-warm-proof@1'
_CACHE = WeakKeyDictionary()
_LOCK = RLock()
_MAX_QUERIES = 4096
_MAX_BYTES = 16 * 1024 * 1024
_MAX_WRITERS = 8


@dataclass
class _Proof:
    scope: tuple
    prefix: object
    digest: str
    responses: dict = field(default_factory=dict)
    response_bytes: int = 0


def selected(source):
    installed = source.installed_payload
    if installed is None:
        return False
    rules = installed.get('strategy', {}).get('numbered_release', {}).get('contract', {}).get('rule_set_contracts', ())
    if RULE not in rules:
        return False
    if type(rules) is not list or rules.count(RULE) != 1:
        raise ValueError('Warm proof requires one exact declared rule')
    source.require_installed_admission()
    return True


def _authority(client, run_id, max_commits, first_price_source):
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from .arte_typed_insert_dispatch import TypedInsertDispatch
    from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
    from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
    from src.backend.backtest_fixed_structural_lot_management import recovery_contexts_by_batch
    profile = require_fixed_structural_lot_profile(client.fixed_structural_lot_profile)
    source = profile.operation.source
    source.require_installed_admission()
    if not selected(source) or source.run_id != run_id or (
            first_price_source is not None and first_price_source is not source.price_authority):
        raise ValueError('Warm proof has foreign source/run/price authority')
    contexts = tuple(getattr(client, 'fixed_structural_lot_contexts', ()))
    recoveries = tuple(getattr(client, 'fixed_lot_recovery_contexts', ()))
    if any(context.source is not source for context in contexts):
        raise ValueError('Warm proof has foreign entry source')
    # These existing owners reject mutation, forged admission and foreign recovery.
    fixed_lot_contexts_by_batch(run_id, contexts, max_commits=max_commits)
    recovery_contexts_by_batch(run_id, recoveries, max_commits=max_commits)
    lease = getattr(client, 'backtest_v4_lease', None)
    dispatch = getattr(client, 'typed_insert_dispatch', None)
    if (not isinstance(lease, BacktestV4KeeperLease) or lease.run_id != run_id
            or lease.epoch < 1 or getattr(client, 'typed_insert_strict', False) is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or dispatch.keeper is not lease.owner._session.client):
        raise RuntimeError('Warm proof lacks its original exclusive writer')
    lease.assert_current()
    gate, _ = dispatch._read_gate(run_id)
    if (gate.mode != 'open' or gate.inflight or gate.registered
            or gate.active_batch_id != str(UUID(int=0)) or gate.compacted_through < 1):
        raise RuntimeError('Warm proof has an unsealed dispatch gate')
    scope = (source, source.installed_json, source.selected_configuration_hash,
             source.price_authority, lease, lease.epoch, dispatch,
             tuple(contexts), tuple(recoveries))
    return source, contexts, recoveries, lease, gate, scope


def load_prefix(client, run_id, *, max_commits=100_000, first_price_source=None):
    from .arte_journal_commit_v4 import load_verified_v4_prefix
    from .arte_journal_writer import _CONTRACTS, _literal, _rows
    from .journal_contract import canonical_json
    if type(max_commits) is not int or not 1 <= max_commits <= 100_000:
        raise ValueError('Warm proof needs a bounded committed chain')
    with _LOCK:
        source, contexts, recoveries, lease, gate, scope = _authority(
            client, run_id, max_commits, first_price_source)
        columns = ','.join(name for name, _ in _CONTRACTS['trading_commit_v4'].columns)
        rows = _rows(client, f'SELECT {columns} FROM arte.trading_commit_v4 '
                     f'WHERE run_id={_literal(run_id)} '
                     f'AND batch_id=toUUID({_literal(gate.compacted_batch_id)}) LIMIT 2 FORMAT JSONEachRow')
        digest = sha256(canonical_json(rows[0]).encode()).hexdigest() if len(rows) == 1 else None
        if (digest != gate.compacted_commit_hash or rows[0]['last_sequence'] != gate.compacted_through
                or rows[0]['run_id'] != run_id or rows[0]['status'] != 'running'):
            _CACHE.pop(client, None)
            raise RuntimeError('Warm proof head differs from Keeper compaction')
        cached = _CACHE.get(client)
        if (cached is not None and cached.scope == scope and cached.digest == digest
                and cached.prefix.last_sequence == gate.compacted_through
                and cached.prefix.last_batch_id == gate.compacted_batch_id):
            if len(cached.prefix.batch_ids) > max_commits:
                raise RuntimeError('Warm proof exceeds bounded committed chain')
            lease.assert_current()
            return cached.prefix
        # Every initial/new frontier and changed context or lease is fully cold verified.
        _CACHE.pop(client, None)
        prefix = load_verified_v4_prefix(client, run_id, max_commits=max_commits,
            first_price_source=source.price_authority, fixed_lot_contexts=contexts,
            fixed_lot_recovery_contexts=recoveries)
        if (prefix is None or prefix.last_sequence != gate.compacted_through
                or prefix.last_batch_id != gate.compacted_batch_id or prefix.status != 'running'):
            raise RuntimeError('Warm proof full prefix differs from Keeper compaction')
        if (cached is not None and cached.prefix.run_id == run_id
                and (prefix.last_sequence < cached.prefix.last_sequence
                     or prefix.batch_ids[:len(cached.prefix.batch_ids)] != cached.prefix.batch_ids)):
            raise RuntimeError('Warm proof frontier is not an append-only extension')
        lease.assert_current()
        current, _ = client.typed_insert_dispatch._read_gate(run_id)
        if current != gate:
            raise RuntimeError('Warm proof frontier changed during verification')
        if client not in _CACHE and len(_CACHE) >= _MAX_WRITERS:
            raise RuntimeError('Warm proof exceeds bounded exclusive writer inventory')
        _CACHE[client] = _Proof(scope, prefix, digest)
        return prefix


class _ImmutableReads:
    """Replay bounded SELECT transport bytes, never recovered mutable OMS objects."""
    def __init__(self, client, retained):
        self.client = client
        self.retained = retained
        self.new = {}
        self.new_bytes = 0
        self.cache_eligible = True

    def __getattr__(self, name):
        return getattr(self.client, name)

    def execute(self, sql, *args, **kwargs):
        if args or kwargs or not sql.lstrip().upper().startswith('SELECT '):
            raise ValueError('Warm proof transport accepts exact SELECT text only')
        if sql in self.retained:
            return self.retained[sql]
        result = self.client.execute(sql)
        if type(result) is not str:
            raise ValueError('Warm proof requires immutable text transport')
        size = len(sql.encode()) + len(result.encode())
        if self.cache_eligible:
            if len(self.new) >= _MAX_QUERIES or self.new_bytes + size > _MAX_BYTES:
                # Only optional memoization is declined. The actual complete
                # reader keeps every returned row and all integrity checks.
                self.cache_eligible = False
                self.new.clear()
                self.new_bytes = 0
            else:
                self.new[sql] = result
                self.new_bytes += size
        return result


def load_oms_groups(client, prefix, **kwargs):
    from .arte_oms_projection import _load_latest_committed_oms_groups
    profile = getattr(client, 'fixed_structural_lot_profile', None)
    if (profile is None or not selected(profile.operation.source)
            or getattr(client, 'backtest_v4_lease', None) is None):
        return _load_latest_committed_oms_groups(client, prefix, **kwargs)
    # A cold reader does not have the issued exclusive profile/lease capability.
    current = load_prefix(client, prefix.run_id)
    if current != prefix:
        raise RuntimeError('Warm OMS proof has a foreign or stale prefix')
    with _LOCK:
        proof = _CACHE[client]
        proxy = _ImmutableReads(client, proof.responses)
        # Always reconstruct and validate typed rows, lineage, quantities and ownership.
        # Publish transport proofs only after the complete bounded reader succeeds.
        result = _load_latest_committed_oms_groups(proxy, prefix, **kwargs)
        load_prefix(client, prefix.run_id)
        if _CACHE.get(client) is not proof:
            raise RuntimeError('Warm OMS proof context/frontier changed during read')
        new_bytes = sum(len(k.encode()) + len(v.encode()) for k, v in proxy.new.items())
        if proxy.cache_eligible and len(proof.responses) + len(proxy.new) <= _MAX_QUERIES and proof.response_bytes + new_bytes <= _MAX_BYTES:
            proof.responses.update(proxy.new)
            proof.response_bytes += new_bytes
        return result

"""Private synchronous checkpoint proof scope; never a recovery/write grant.

The final full proof is intentional: source authorities contain producer plans,
and an object identity or a journal seal cannot attest unchanged source bytes.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
import sys
from threading import get_ident

from .journal_contract import canonical_json


_CONSTRUCTION_KEY = object()
_FULL_OPERATION = ContextVar('checkpoint_full_prefix_operation', default=None)
_CHECKPOINT_OBSERVATION = ContextVar('checkpoint_content_observation', default=None)
_MEMO_ENTRY_LIMIT = 512
_MEMO_BYTE_LIMIT = 32 * 1024 * 1024


def _typed_key(value):
    """Closed scalar grammar prevents JSON bool/int and custom-repr aliases."""
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return (kind.__name__, value)
    if kind is float and isfinite(value):
        return ('float', value.hex())
    if kind is Decimal and value.is_finite():
        return ('Decimal', str(value))
    if kind in (date, datetime):
        return (kind.__name__, value.isoformat())
    if kind in (tuple, list):
        return (kind.__name__, tuple(_typed_key(v) for v in value))
    if kind is dict and all(type(k) is str for k in value):
        return ('dict', tuple((k, _typed_key(value[k])) for k in sorted(value)))
    if is_dataclass(value) and not isinstance(value, type):
        return (kind.__module__, kind.__qualname__, tuple(
            (f.name, _typed_key(getattr(value, f.name))) for f in fields(value)))
    raise TypeError('Checkpoint memo key is outside its closed scalar grammar')


def _retained_size(value, seen=None, depth=0):
    """Count retained builtin containers/dataclass fields without arbitrary repr."""
    if seen is None:
        seen = set()
    if depth > 64:
        return _MEMO_BYTE_LIMIT + 1
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if type(value) is dict:
        children = tuple(value.keys()) + tuple(value.values())
    elif type(value) in (tuple, list):
        children = value
    elif is_dataclass(value) and not isinstance(value, type):
        children = tuple(getattr(value, f.name) for f in fields(value))
    else:
        children = ()
    for child in children:
        size += _retained_size(child, seen, depth + 1)
        if size > _MEMO_BYTE_LIMIT:
            break
    return size


class _MemoPool:
    __slots__ = ('memo', 'ledger', 'bytes')

    def __init__(self):
        self.memo, self.ledger, self.bytes = {}, {}, 0


class _FullPrefixOperation:
    __slots__ = ('client', 'run_id', 'source', 'thread', 'prefix', 'digest',
                 'count', 'last_sequence', 'last_batch_id', 'last_commit_hash', 'collector', 'pool')

    def __init__(self, client, run_id, source, pool=None):
        self.client, self.run_id, self.source = client, run_id, source
        self.thread, self.prefix = get_ident(), None
        self.digest = sha256(b'checkpoint-verified-commit-chain@1').digest()
        self.count, self.last_sequence, self.last_batch_id = 0, 0, None
        self.last_commit_hash = None
        self.collector = None
        self.pool = _MemoPool() if pool is None else pool

    def matches(self, client, source):
        return (client is self.client and source is self.source
                and get_ident() == self.thread)


@contextmanager
def _full_prefix_operation(client, run_id, source):
    """Nested proofs share completed ancestry only under the same outer owner.

    Every call still builds its own independently verified chain ledger. The
    final checkpoint proof starts after the first outer call has exited, so it
    receives an entirely new pool and cannot reuse earlier source approval.
    """
    parent = _FULL_OPERATION.get()
    pool = (parent.pool if parent is not None and parent.matches(client, source)
            and parent.run_id == run_id else None)
    operation = _FullPrefixOperation(client, run_id, source, pool)
    token = _FULL_OPERATION.set(operation)
    try:
        yield
    finally:
        if pool is None:
            operation.pool.memo.clear()
            operation.pool.ledger.clear()
            operation.pool.bytes = 0
        operation.prefix = None
        operation.collector = None
        _FULL_OPERATION.reset(token)


def _register_verified_predecessor(client, source, prefix):
    from .arte_journal_commit_v4 import V4CommittedPrefix
    operation = _FULL_OPERATION.get()
    if operation is None or not operation.matches(client, source):
        return
    if prefix is None:
        if operation.count:
            raise RuntimeError('Checkpoint memo predecessor disappeared')
    elif (type(prefix) is not V4CommittedPrefix or prefix.run_id != operation.run_id
            or prefix.status != 'running' or len(prefix.batch_ids) != operation.count
            or prefix.last_sequence != operation.last_sequence
            or prefix.last_batch_id != operation.last_batch_id):
        raise RuntimeError('Checkpoint memo predecessor is not its verified chain')
    operation.prefix = prefix


def _record_verified_commit(client, source, commit):
    operation = _FULL_OPERATION.get()
    if operation is not None and operation.matches(client, source):
        # Called only AFTER the actual complete batch readback equals inventory.
        operation.digest = sha256(operation.digest + canonical_json(commit).encode()).digest()
        operation.count += 1
        operation.last_sequence = commit['last_sequence']
        operation.last_batch_id = commit['batch_id']
        operation.last_commit_hash = sha256(canonical_json(commit).encode()).hexdigest()
        # A successful checkpoint can be remembered before its containing exit
        # batch finishes. Historical reuse waits for that batch's COMPLETE proof.
        for key, witness in tuple(operation.pool.ledger.items()):
            if (witness[2] == commit['batch_id'] and witness[3] is None
                    and witness[4] < commit['first_sequence']):
                operation.pool.ledger[key] = (*witness[:3],
                    operation.last_commit_hash, witness[4])


def _encoded_prefix(prefix):
    return json.dumps(_typed_key(prefix), separators=(',', ':'),
                      allow_nan=False).encode()


def _historical_digest(operation, client, prefix, event):
    """Resolve ONLY an internally witnessed subset, with fresh metadata reads.

    Equality with caller inputs is insufficient: the exact earlier checkpoint
    and its containing source batch must have independently completed proof in
    this outer operation. Derive the predecessor again from the owned ordered
    chain and actual normalized commit columns before looking up its memo.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_writer import _CONTRACTS, _literal, _rows
    if (type(prefix) is not V4CommittedPrefix or type(event) is not dict
            or type(event.get('batch_id')) is not str
            or operation.prefix is None or prefix.run_id != operation.run_id
            or type(prefix.last_sequence) is not int
            or prefix.last_sequence >= operation.prefix.last_sequence
            or not prefix.batch_ids or prefix.status != 'running'
            or prefix.last_batch_id != prefix.batch_ids[-1]):
        return None
    count = len(prefix.batch_ids)
    if (count >= len(operation.prefix.batch_ids)
            or prefix.batch_ids != operation.prefix.batch_ids[:count]
            or event['batch_id'] != operation.prefix.batch_ids[count]):
        return None
    try:
        prefix_key = _encoded_prefix(prefix)
    except (TypeError, ValueError, RecursionError):
        return None
    witness = operation.pool.ledger.get((prefix_key, event['batch_id']))
    if witness is None or witness[3] is None:
        return None
    columns = ','.join(name for name, _ in _CONTRACTS['trading_commit_v4'].columns)
    def read(batch_id, expected_hash):
        rows = _rows(client, f'SELECT {columns} FROM arte.trading_commit_v4 '
            f'WHERE run_id={_literal(operation.run_id)} '
            f'AND batch_id=toUUID({_literal(batch_id)}) LIMIT 2 FORMAT JSONEachRow')
        if (len(rows) != 1 or rows[0]['run_id'] != operation.run_id
                or rows[0]['batch_id'] != batch_id
                or sha256(canonical_json(rows[0]).encode()).hexdigest() != expected_hash):
            raise RuntimeError('Historical checkpoint commit differs from its complete proof')
        return rows[0]
    prior = read(prefix.last_batch_id, witness[1])
    source = read(event['batch_id'], witness[3])
    if (prior['status'] != 'running' or source['prior_batch_id'] != prior['batch_id']
            or source['first_sequence'] != prior['last_sequence'] + 1
            or source['run_month'] != prior['run_month']):
        raise RuntimeError('Historical checkpoint predecessor is not its proved source chain')
    derived = V4CommittedPrefix(operation.run_id, prior['last_sequence'],
        prior['batch_id'], prior['source_cursor'], prior['status'],
        operation.prefix.batch_ids[:count])
    if _encoded_prefix(derived) != prefix_key:
        return None
    return witness[0]


def _remember_checkpoint(operation, prefix, event):
    """Retain compact boundary metadata only for a successful checkpoint."""
    if (type(event) is not dict or type(event.get('batch_id')) is not str
            or type(event.get('sequence')) is not int
            or event['sequence'] <= prefix.last_sequence
            or operation.last_commit_hash is None):
        return
    try:
        key = (_encoded_prefix(prefix), event['batch_id'])
    except (TypeError, ValueError, RecursionError):
        return
    if key in operation.pool.ledger:
        return
    value = (operation.digest.hex(), operation.last_commit_hash,
             event['batch_id'], None, prefix.last_sequence)
    size = _retained_size(key) + _retained_size(value) + 512
    if (len(operation.pool.memo) + len(operation.pool.ledger) < _MEMO_ENTRY_LIMIT
            and operation.pool.bytes + size <= _MEMO_BYTE_LIMIT):
        operation.pool.ledger[key] = value
        operation.pool.bytes += size


def _lineage_content(lineage):
    """Only the unchanged entry-family reducer has a nonrecursive raw path."""
    from dataclasses import replace
    if any(item.source_intent.intent.action != 'enter_long' for item in lineage):
        return None
    return tuple(replace(item, source_intent=replace(item.source_intent,
                 source_batch=None)) for item in lineage)


def _observe_checkpoint_financial(client, prefix, context, cursor, image, lineage,
                                  *, first_price_source, diagnostic):
    """No-op outside the private reconstruction's exact owned collector."""
    if diagnostic is None:
        return
    collector = _CHECKPOINT_OBSERVATION.get()
    operation = _FULL_OPERATION.get()
    if (type(collector) is not dict or operation is None
            or operation.collector is not collector
            or collector.get('operation') is not operation
            or not operation.matches(client, first_price_source)
            or operation.run_id != prefix.run_id or collector['client'] is not client
            or collector['source'] is not first_price_source
            or collector['prefix'] is not prefix or collector['diagnostic'] is not diagnostic
            or collector['thread'] != get_ident()):
        return
    content = _lineage_content(lineage)
    if content is not None:
        collector['financial'] = (context, cursor, image, content)


def _observe_checkpoint_completed(client, prefix, cursor, rows, state,
                                  *, first_price_source, diagnostic):
    """Capture only after BOTH original consumers finish every validator."""
    collector = _CHECKPOINT_OBSERVATION.get()
    operation = _FULL_OPERATION.get()
    if (type(collector) is not dict or operation is None
            or operation.collector is not collector
            or collector.get('operation') is not operation
            or not operation.matches(client, first_price_source)
            or operation.run_id != prefix.run_id or collector['client'] is not client
            or collector['source'] is not first_price_source
            or collector['prefix'] is not prefix or collector['diagnostic'] is not diagnostic
            or collector['thread'] != get_ident() or 'financial' not in collector):
        return
    collector['content'] = sha256(canonical_json(
        (cursor, rows, state, collector['financial'])).encode()).hexdigest()


def _fresh_checkpoint_content(client, prefix, diagnostic, first_price_source):
    """Fresh actual raw/schema/semantic guards, no recursive source ancestry.

    The initial witness comes from already-read and fully validated rows. The
    historical hit re-reads children and re-runs the same reducers; source-batch
    certificates remain outside memo in the enclosing diagnostic/entry sealers.
    Unsupported exit-family ancestry takes the entire unchanged reconstruction.
    """
    from dataclasses import replace
    from .confirmed_original_risk_failure import OriginalRiskDecisionDiagnostic
    if type(diagnostic) is not OriginalRiskDecisionDiagnostic:
        return None
    from .strategy_one_management_snapshot import (
        load_unattested_manager_snapshot_rows, restore_manager_snapshot,
        attach_committed_momentum_sources,
    )
    from .strategy_one_broker_match_snapshot import (
        load_unattested_broker_match_snapshot, verify_broker_match_snapshot,
    )
    from .arte_journal_projection import load_latest_backtest_cursor
    from .arte_journal_writer import load_typed_run_context
    from .arte_journal_reader import load_complete_typed_protection_history
    from .arte_oms_projection import (load_latest_committed_oms_groups,
        load_committed_oms_admission_page, load_committed_oms_decision_page,
        reconstruct_strategy_one_oms_lineage, _approved_strategy_one_oms_intent,
        RecoveredStrategyOneOmsLineage)
    from .arte_intent_projection import load_committed_strategy_intent_page
    sequence = diagnostic.checkpoint.source_manager_checkpoint_sequence
    ceiling = replace(prefix, last_sequence=sequence)
    context = load_typed_run_context(client, prefix.run_id)
    cursor = load_latest_backtest_cursor(client, ceiling)
    manager = load_unattested_manager_snapshot_rows(client,
        run_id=prefix.run_id, checkpoint_sequence=sequence)
    state = attach_committed_momentum_sources(client, prefix,
        restore_manager_snapshot(manager), first_price_source=first_price_source)
    broker = verify_broker_match_snapshot(load_unattested_broker_match_snapshot(
        client, run_id=prefix.run_id, checkpoint_sequence=sequence))
    history = load_complete_typed_protection_history(client, ceiling,
        page_size=1000, max_events=100000)
    groups = load_latest_committed_oms_groups(client, ceiling, page_size=500,
        max_transitions=20000, allowed_accounts=frozenset(context['account_ids']),
        strategy_identity=(context['strategy_id'], context['strategy_revision']),
        require_tactic=True)
    if len(groups) > 2000:
        raise RuntimeError('Checkpoint content OMS inventory exceeds its original bound')
    admissions = load_committed_oms_admission_page(client, ceiling, groups, max_rows=4096)
    decisions = load_committed_oms_decision_page(client, ceiling, groups, admissions, max_rows=4096)
    identifiers = sorted({group.intent_record_id for group in groups})
    intents = {}
    for start in range(0, len(identifiers), 500):
        chunk = tuple(identifiers[start:start+500])
        values = load_committed_strategy_intent_page(client, ceiling,
            limit=len(chunk), record_ids=chunk, include_source_batch=False)
        if len(values) != len(chunk):
            raise RuntimeError('Checkpoint content intent inventory is incomplete')
        for value in values:
            if value.record_id in intents or value.intent.action != 'enter_long':
                raise RuntimeError('Checkpoint entry ancestry changed or is unsupported')
            intents[value.record_id] = value
    if set(intents) != set(identifiers):
        raise RuntimeError('Checkpoint content intent identities changed')
    lineage = tuple(RecoveredStrategyOneOmsLineage(group, intents[group.intent_record_id],
        reconstruct_strategy_one_oms_lineage(group, intents[group.intent_record_id], history,
            admission_reservation=admissions[group.sequence],
            admission_decision=decisions[group.sequence]), history.through_sequence,
        _approved_strategy_one_oms_intent(group, intents[group.intent_record_id], history,
            admissions[group.sequence], decisions[group.sequence])[0],
        dict(admissions[group.sequence])) for group in groups)
    return sha256(canonical_json((cursor, manager, state,
        (context, cursor, broker, lineage))).encode()).hexdigest()


def _reconstruct_original_risk_checkpoint(client, prefix, failure, parent,
                                          event, diagnostic, *, first_price_source):
    from .original_risk_checkpoint import _load_original_risk_checkpoint
    operation = _FULL_OPERATION.get()
    key, digest = None, None
    current = (operation is not None and operation.matches(client, first_price_source)
               and prefix is not None and prefix is operation.prefix)
    if current:
        digest = operation.digest.hex()
    elif operation is not None and operation.matches(client, first_price_source):
        digest = _historical_digest(operation, client, prefix, event)
    if digest is not None:
        try:
            key = json.dumps(_typed_key((operation.run_id, digest,
                prefix, failure, parent, event, diagnostic)),
                separators=(',', ':'), allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError):
            # Unsupported/oversized facts still take the unchanged full reader.
            key = None
        if key is not None and len(key) > _MEMO_BYTE_LIMIT:
            key = None
        if key is not None and key in operation.pool.memo:
            cached, content = operation.pool.memo[key]
            if _fresh_checkpoint_content(client, prefix, diagnostic,
                    first_price_source) != content:
                raise RuntimeError('Checkpoint normalized content changed after its complete proof')
            return deepcopy(cached)
    collector = {'operation': operation, 'client': client, 'source': first_price_source, 'prefix': prefix,
                 'diagnostic': diagnostic, 'thread': get_ident()}
    previous_collector = operation.collector if key is not None else None
    if key is not None:
        operation.collector = collector
    token = _CHECKPOINT_OBSERVATION.set(collector) if key is not None else None
    try:
        result = _load_original_risk_checkpoint(client, prefix, failure, parent, event,
                                                diagnostic, first_price_source=first_price_source)
        observed_content = collector.get('content')
    finally:
        if token is not None:
            _CHECKPOINT_OBSERVATION.reset(token)
            operation.collector = previous_collector
        collector.clear()
    if key is not None:
        from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
        if type(result) is OriginalRiskManagementState:
            from .confirmed_original_risk_failure import OriginalRiskDecisionDiagnostic
            content = observed_content
            if type(diagnostic) is OriginalRiskDecisionDiagnostic and content is None:
                return result  # unsupported ancestry: no omissions, unchanged reader
            size = _retained_size(key) + _retained_size(result) + _retained_size(content) + 256
            if (len(operation.pool.memo) + len(operation.pool.ledger) < _MEMO_ENTRY_LIMIT
                    and operation.pool.bytes + size <= _MEMO_BYTE_LIMIT):
                operation.pool.memo[key] = (deepcopy(result), content)
                operation.pool.bytes += size
            if current:
                _remember_checkpoint(operation, prefix, event)
    return result


def _inventory_digest(client, run_id):
    from .arte_journal_writer import _CONTRACTS, _literal, _rows
    columns = ','.join(name for name, _ in _CONTRACTS['trading_commit_v4'].columns)
    rows = _rows(client, f'SELECT {columns} FROM arte.trading_commit_v4 '
                 f'WHERE run_id={_literal(run_id)} ORDER BY first_sequence,batch_id '
                 'LIMIT 100001 FORMAT JSONEachRow')
    if not rows or len(rows) > 100_000:
        raise RuntimeError('Checkpoint operation lacks bounded commit inventory')
    return sha256(canonical_json(rows).encode()).hexdigest()


class _CheckpointPrefixReadScope:
    """Only the local full-proof factory constructs this short-lived scope."""
    __slots__ = ('_client', '_source', '_prefix', '_thread', '_active')

    def __init__(self, key, client, source, prefix):
        if key is not _CONSTRUCTION_KEY:
            raise ValueError('Checkpoint scope requires its independent proof factory')
        self._client, self._source, self._prefix = client, source, prefix
        self._thread, self._active = get_ident(), True

    def _prefix_for(self, client, run_id, sequence, source):
        if (not self._active or get_ident() != self._thread
                or client is not self._client or source is not self._source
                or type(run_id) is not str or run_id != self._prefix.run_id
                or type(sequence) is not int or sequence != self._prefix.last_sequence):
            raise ValueError('Checkpoint scope crosses its operation, reader or source')
        return self._prefix


def _scoped_prefix(scope, client, run_id, sequence, source):
    if type(scope) is not _CheckpointPrefixReadScope:
        raise ValueError('Checkpoint reader requires the exact private proof scope')
    return scope._prefix_for(client, run_id, sequence, source)


@contextmanager
def _checkpoint_prefix_scope(client, *, run_id, checkpoint_sequence,
                             first_price_source):
    """Share initial proof; independently reprove all details/sources on exit.

    No global memoization, caller-supplied prefix, async lifetime or cached
    source approval. Exceptions invalidate the scope without selecting roots.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1):
        raise ValueError('Checkpoint operation requires exact run and sequence')
    def read():
        return load_verified_v4_prefix(client, run_id, **(
            {} if first_price_source is None else {'first_price_source': first_price_source}))
    digest = _inventory_digest(client, run_id)
    prefix = read()
    if (type(prefix) is not V4CommittedPrefix or prefix.run_id != run_id
            or prefix.last_sequence != checkpoint_sequence or prefix.status != 'running'
            or not prefix.batch_ids or prefix.last_batch_id != prefix.batch_ids[-1]
            or _inventory_digest(client, run_id) != digest):
        raise RuntimeError('Checkpoint operation inventory changed or crosses its receipt')
    scope = _CheckpointPrefixReadScope(_CONSTRUCTION_KEY, client, first_price_source, prefix)
    try:
        yield scope
        scope._prefix_for(client, run_id, checkpoint_sequence, first_price_source)
        # This invokes actual normalized-column hash verification AND every
        # source/financial semantic sealer, including mutable producer plans.
        if read() != prefix or _inventory_digest(client, run_id) != digest:
            raise RuntimeError('Checkpoint operation proof changed before acceptance')
    finally:
        scope._active = False

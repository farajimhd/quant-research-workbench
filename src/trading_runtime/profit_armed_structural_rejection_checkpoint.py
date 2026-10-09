"""Versioned standalone causal checkpoint codec and replay, not native authority.

Hashes establish deterministic content integrity only. Restore requires exact
independently supplied source, declaration, entry/first-held/risk and cursor
bindings. A native owner must additionally verify committed manager/broker/
Portfolio/OMS roots and their attested heads before using this state.
"""
from dataclasses import dataclass, fields, replace
from hashlib import sha256
import json

from .profit_armed_structural_rejection import (
    StructuralRejectionPolicy, RejectionSource, HeldBar, FrozenResistance,
    CurrentQuote, StructuralRejectionState, StructuralRejectionInput,
    StructuralRejectionWitness, reduce_structural_rejection,
)

VERSION = 'profit-armed-structural-rejection-checkpoint@1'
MAX_CHECKPOINT_BYTES = 1_048_576
MAX_REPLAY_INPUTS = 65_536
_TYPES = {t.__name__: t for t in (StructuralRejectionPolicy, RejectionSource,
    HeldBar, FrozenResistance, CurrentQuote, StructuralRejectionState,
    StructuralRejectionWitness)}


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= (1 << 64) - 1:
        raise ValueError('Exact bounded checkpoint integer required')


def _tree(value):
    """Closed typed tree; finite floats use hex, preserving signed zero exactly."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        return {'float64': value.hex()}
    if type(value) is tuple:
        return {'tuple': [_tree(v) for v in value]}
    if type(value).__name__ in _TYPES and _TYPES[type(value).__name__] is type(value):
        return {'type': type(value).__name__, 'fields': {
            f.name: _tree(getattr(value, f.name)) for f in fields(value)}}
    raise ValueError('Unsupported checkpoint value/type')


def _untree(value, bars=None):
    if bars is None:
        bars = {}
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is not dict:
        raise ValueError('Exact typed checkpoint tree required')
    if set(value) == {'float64'} and type(value['float64']) is str:
        result = float.fromhex(value['float64'])
        if result.hex() != value['float64']:
            raise ValueError('Noncanonical Float64 checkpoint scalar')
        return result
    if set(value) == {'tuple'} and type(value['tuple']) is list:
        return tuple(_untree(v, bars) for v in value['tuple'])
    if (set(value) != {'type', 'fields'} or type(value['type']) is not str
            or value['type'] not in _TYPES or type(value['fields']) is not dict):
        raise ValueError('Unknown checkpoint class/fields')
    cls = _TYPES[value['type']]
    if set(value['fields']) != {f.name for f in fields(cls)}:
        raise ValueError('Incomplete/foreign checkpoint fields')
    # The reducer retains the same immutable observation in multiple roles.
    # NaN != NaN, so independently creating those equal-content observations
    # would break its unchanged same-clock guard. Preserve sharing only for
    # identical complete HeldBar trees within this single decode operation.
    key = _bytes(value) if cls is HeldBar else None
    if key is not None and key in bars:
        return bars[key]
    result = cls(**{k: _untree(v, bars) for k, v in value['fields'].items()})
    if key is not None:
        bars[key] = result
    return result


def _bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hash(value):
    return sha256(_bytes(_tree(value))).hexdigest()


@dataclass(frozen=True, slots=True)
class StructuralRejectionCheckpointBinding:
    """Expected external pins; this value itself is not an issued capability."""
    source: RejectionSource
    policy: StructuralRejectionPolicy
    position_intent_id: str
    account_id: str
    session_origin_us: int
    first_held_boundary_ms: int
    original_ask_int: int
    original_stop_int: int
    checkpoint_sequence: int
    boundary_ms: int

    def __post_init__(self):
        _integer(self.checkpoint_sequence, 1); _integer(self.boundary_ms)
        initial = StructuralRejectionState(self.source, self.position_intent_id,
            self.account_id, self.session_origin_us, self.first_held_boundary_ms,
            self.original_ask_int, self.original_stop_int, policy=self.policy)
        initial.__post_init__()
        if self.boundary_ms % self.policy.decision_resolution_ms:
            raise ValueError('Checkpoint boundary differs from declared decision clock')


@dataclass(frozen=True, slots=True)
class StructuralRejectionCheckpoint:
    version: str
    checkpoint_sequence: int
    state: StructuralRejectionState
    firing_witness: StructuralRejectionWitness | None
    state_hash: str
    witness_hash: str
    content_hash: str


def _body(sequence, state, witness):
    return dict(version=VERSION, checkpoint_sequence=sequence, state=_tree(state),
        firing_witness=_tree(witness), state_hash=_hash(state), witness_hash=_hash(witness))


def _validate_state_witness(state, witness):
    if type(state) is not StructuralRejectionState:
        raise ValueError('Exact checkpoint reducer state required')
    state.__post_init__()
    _integer(state.last_decision_boundary_ms)
    if state.last_decision_boundary_ms % state.policy.decision_resolution_ms:
        raise ValueError('Checkpoint state has nondeclared decision frontier')
    if not state.fired:
        if witness is not None:
            raise ValueError('Unfired checkpoint cannot carry a firing witness')
        return
    if type(witness) is not StructuralRejectionWitness:
        raise ValueError('Fired checkpoint requires complete causal firing witness')
    # Validate with the actual unchanged reducer; a hash or structural fired
    # flag cannot stand in for activity, fresh quote, priority and predecessor.
    if (type(witness.predecessor) is not StructuralRejectionState
            or witness.policy != state.policy or witness.predecessor.source != state.source
            or witness.decision_boundary_ms > state.last_decision_boundary_ms):
        raise ValueError('Foreign/future checkpoint firing predecessor')
    value = StructuralRejectionInput(state.source, witness.decision_boundary_ms,
        witness.rejections[-1] if witness.rejections else None, witness.resistance,
        witness.activity, witness.quote)
    replayed, generated = reduce_structural_rejection(witness.predecessor, value,
                                                     policy=state.policy)
    if generated is None or _tree(generated) != _tree(witness):
        raise ValueError('Checkpoint firing witness does not replay its causal decision')
    replayed = replace(replayed, last_decision_boundary_ms=state.last_decision_boundary_ms)
    if _tree(replayed) != _tree(state):
        raise ValueError('Checkpoint fired state differs from witnessed transition')


def checkpoint_structural_rejection(state, *, checkpoint_sequence, firing_witness=None):
    """Create content-integrity packet; performs no native publication/attestation."""
    _integer(checkpoint_sequence, 1)
    _validate_state_witness(state, firing_witness)
    body = _body(checkpoint_sequence, state, firing_witness)
    if len(_bytes(body)) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Checkpoint exceeds bounded encoded size')
    return StructuralRejectionCheckpoint(VERSION, checkpoint_sequence, state,
        firing_witness, body['state_hash'], body['witness_hash'], sha256(_bytes(body)).hexdigest())


def restore_structural_rejection_checkpoint(checkpoint, *, binding):
    """Restore only under exact independently supplied pins, never selfhash authority."""
    if (type(checkpoint) is not StructuralRejectionCheckpoint
            or type(binding) is not StructuralRejectionCheckpointBinding):
        raise ValueError('Exact typed checkpoint and independent binding required')
    binding.__post_init__()
    rebuilt = checkpoint_structural_rejection(checkpoint.state,
        checkpoint_sequence=checkpoint.checkpoint_sequence,
        firing_witness=checkpoint.firing_witness)
    if (checkpoint.version != VERSION or checkpoint.state_hash != rebuilt.state_hash
            or checkpoint.witness_hash != rebuilt.witness_hash
            or checkpoint.content_hash != rebuilt.content_hash):
        raise ValueError('Checkpoint content/hash/version mismatch')
    state = checkpoint.state
    for name in ('source', 'policy', 'position_intent_id', 'account_id', 'session_origin_us',
                 'first_held_boundary_ms', 'original_ask_int', 'original_stop_int'):
        if _tree(getattr(state, name)) != _tree(getattr(binding, name)):
            raise ValueError('Checkpoint differs from independent source/policy/entry binding')
    if (checkpoint.checkpoint_sequence != binding.checkpoint_sequence
            or state.last_decision_boundary_ms != binding.boundary_ms):
        raise ValueError('Checkpoint differs from exact committed cursor binding')
    return state


def encode_structural_rejection_checkpoint(checkpoint, *, binding):
    restore_structural_rejection_checkpoint(checkpoint, binding=binding)
    body = _body(checkpoint.checkpoint_sequence, checkpoint.state, checkpoint.firing_witness)
    data = _bytes({**body, 'content_hash': checkpoint.content_hash})
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Checkpoint exceeds bounded encoded size')
    return data


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate checkpoint object field')
        result[key] = value
    return result


def decode_structural_rejection_checkpoint(data, *, binding):
    if type(data) is not bytes or not 0 < len(data) <= MAX_CHECKPOINT_BYTES:
        raise ValueError('Exact bounded checkpoint bytes required')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Noncanonical JSON scalar')))
        expected = {'version','checkpoint_sequence','state','firing_witness',
                    'state_hash','witness_hash','content_hash'}
        if type(value) is not dict or set(value) != expected:
            raise ValueError('Unknown/incomplete checkpoint envelope')
        bars = {}
        result = StructuralRejectionCheckpoint(value['version'], value['checkpoint_sequence'],
            _untree(value['state'], bars), _untree(value['firing_witness'], bars), value['state_hash'],
            value['witness_hash'], value['content_hash'])
        restore_structural_rejection_checkpoint(result, binding=binding)
        # Accept only the canonical spelling/order emitted by this version.
        if encode_structural_rejection_checkpoint(result, binding=binding) != data:
            raise ValueError('Noncanonical checkpoint bytes')
        return result
    except (TypeError, KeyError, OverflowError, RecursionError) as exc:
        raise ValueError('Malformed checkpoint tree') from exc


@dataclass(frozen=True, slots=True)
class StructuralRejectionReplayResult:
    checkpoint: StructuralRejectionCheckpoint
    emitted_witnesses: tuple[StructuralRejectionWitness, ...]
    input_count: int


def replay_structural_rejection_checkpoint(checkpoint, inputs, *, binding,
                                          next_checkpoint_sequence):
    """Replay supplied certified observations; never loads sources or executes orders."""
    state = restore_structural_rejection_checkpoint(checkpoint, binding=binding)
    _integer(next_checkpoint_sequence, 1)
    if (type(inputs) is not tuple or not 1 <= len(inputs) <= MAX_REPLAY_INPUTS
            or next_checkpoint_sequence <= checkpoint.checkpoint_sequence):
        raise ValueError('Exact bounded forward replay/cursor required')
    witness = checkpoint.firing_witness
    emitted = []
    for value in inputs:
        state, decision = reduce_structural_rejection(state, value, policy=binding.policy)
        if decision is not None:
            emitted.append(decision); witness = decision
    result = checkpoint_structural_rejection(state,
        checkpoint_sequence=next_checkpoint_sequence, firing_witness=witness)
    return StructuralRejectionReplayResult(result, tuple(emitted), len(inputs))

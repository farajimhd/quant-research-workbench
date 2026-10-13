"""Exact processed OMS observations under the existing decision commit.

Shared scalar contracts, not strategy-owned storage. These projections neither
query the broker nor publish data. Quantities here reproduce the native OMS
observation; the execution ledger remains the financial quantity authority.
"""
from datetime import datetime, timezone
from dataclasses import asdict
from hashlib import sha256
from uuid import UUID, uuid5

from .journal_contract import canonical_json
from .oms_assignment_observation import AssignmentOrderObservation, ObservedWorkingOrder
from .trading_price_codec import encode_trading_price, decode_trading_price
from .rule_decision_basis_projection import _seal
from .rule_decision_basis_schema import OBSERVATION, OBSERVED_ORDER, UNOBSERVED_ORDER

_ROLES = frozenset({'entry', 'managed_exit', 'profit_target', 'protective_stop', 'trailing_stop', 'protective_exit'})


def observation_digest(observation):
    if (type(observation) is not AssignmentOrderObservation
            or type(observation.pending_acquisition) is not bool or type(observation.pending_reduction) is not bool
            or type(observation.observed_at) is not datetime or observation.observed_at.tzinfo is None
            or observation.observed_at.utcoffset() is None
            or type(observation.working_orders) is not tuple or type(observation.unobserved_order_ids) is not tuple
            or max(len(observation.working_orders), len(observation.unobserved_order_ids)) > 65535
            or any(type(value) is not str or not value for value in
                   (observation.account_id, observation.assignment_id, observation.ticker, *observation.unobserved_order_ids))
            or len(set(observation.unobserved_order_ids)) != len(observation.unobserved_order_ids)):
        raise ValueError('Decision observation lacks exact native identity or bounds')
    ids = set()
    for order in observation.working_orders:
        if (type(order) is not ObservedWorkingOrder or not order.broker_order_id
                or order.broker_order_id in ids or order.broker_order_id in observation.unobserved_order_ids
                or order.role not in _ROLES or type(order.status) is not str or not order.status):
            raise ValueError('Decision observation has foreign or duplicate native orders')
        ids.add(order.broker_order_id)
        for value in (order.remaining_quantity, order.limit_price, order.stop_price):
            encode_trading_price(value, allow_zero=True)
    encode_trading_price(observation.high_water_price, allow_zero=True)
    return sha256(canonical_json(asdict(observation)).encode()).hexdigest()


def _children_hash(rows):
    return sha256(canonical_json([(row['ordinal'], row['content_hash']) for row in rows]).encode()).hexdigest()


def project_decision_observation(basis, observation):
    observation_digest(observation)
    at = datetime.fromisoformat(str(basis['boundary_at']).replace('Z', '+00:00'))
    if at.tzinfo is None: at = at.replace(tzinfo=timezone.utc)  # Declared UTC storage codec.
    if ((observation.account_id, observation.assignment_id, observation.observed_at)
            != (basis['account_id'], basis['assignment_id'], at)):
        raise ValueError('Decision observation differs from its exact causal basis')
    common = {key: basis[key] for key in ('run_id', 'event_month', 'batch_id')}
    identity = UUID(basis['record_id'])
    orders = tuple(_seal(OBSERVED_ORDER, {**common,
        'record_id': str(uuid5(identity, 'observed:' + str(index) + ':' + order.broker_order_id)),
        'parent_record_id': str(identity), 'ordinal': index, 'broker_order_id': order.broker_order_id,
        'role': order.role, 'status': order.status,
        'remaining_quantity_bits': encode_trading_price(order.remaining_quantity, allow_zero=True),
        'limit_price_bits': encode_trading_price(order.limit_price, allow_zero=True),
        'stop_price_bits': encode_trading_price(order.stop_price, allow_zero=True)})
        for index, order in enumerate(observation.working_orders))
    unknown = tuple(_seal(UNOBSERVED_ORDER, {**common,
        'record_id': str(uuid5(identity, 'unobserved:' + str(index) + ':' + broker_id)),
        'parent_record_id': str(identity), 'ordinal': index, 'broker_order_id': broker_id})
        for index, broker_id in enumerate(observation.unobserved_order_ids))
    root = _seal(OBSERVATION, {**common, 'record_id': str(identity), 'decision_id': basis['decision_id'],
        'account_id': observation.account_id, 'assignment_id': observation.assignment_id,
        'listing_id': basis['listing_id'], 'ticker': observation.ticker, 'observed_at': observation.observed_at,
        'pending_acquisition': int(observation.pending_acquisition), 'pending_reduction': int(observation.pending_reduction),
        'high_water_price_bits': encode_trading_price(observation.high_water_price, allow_zero=True),
        'working_order_count': len(orders), 'working_order_hash': _children_hash(orders),
        'unobserved_order_count': len(unknown), 'unobserved_order_hash': _children_hash(unknown)})
    return root, orders, unknown


def restore_decision_observation(basis, root, orders, unknown):
    """Verify exact scalar/child seals; this supplies no prefix authority."""
    families = []
    for table, rows in ((OBSERVATION, (root,)), (OBSERVED_ORDER, tuple(orders)), (UNOBSERVED_ORDER, tuple(unknown))):
        normalized = tuple(_seal(table, {key: value for key, value in row.items() if key != 'content_hash'}) for row in rows)
        if any(row['content_hash'] != source.get('content_hash') for row, source in zip(normalized, rows)):
            raise ValueError('Decision observation scalar hash differs')
        families.append(normalized)
    (root,), orders, unknown = families
    if (root['pending_acquisition'] not in (0, 1) or root['pending_reduction'] not in (0, 1)
            or root['working_order_count'] != len(orders) or root['unobserved_order_count'] != len(unknown)
            or root['working_order_hash'] != _children_hash(orders) or root['unobserved_order_hash'] != _children_hash(unknown)):
        raise ValueError('Decision observation child coverage differs')
    for rows in (orders, unknown):
        for index, row in enumerate(rows):
            if (row['ordinal'] != index or row['parent_record_id'] != root['record_id']
                    or any(row[key] != root[key] for key in ('run_id', 'event_month', 'batch_id'))):
                raise ValueError('Decision observation child lineage differs')
    observation = AssignmentOrderObservation(root['account_id'], root['assignment_id'], root['ticker'],
        datetime.fromisoformat(root['observed_at']), bool(root['pending_acquisition']), bool(root['pending_reduction']),
        tuple(row['broker_order_id'] for row in unknown), tuple(ObservedWorkingOrder(row['broker_order_id'],
            row['role'], decode_trading_price(row['remaining_quantity_bits'], allow_zero=True),
            decode_trading_price(row['limit_price_bits'], allow_zero=True),
            decode_trading_price(row['stop_price_bits'], allow_zero=True), row['status']) for row in orders),
        decode_trading_price(root['high_water_price_bits'], allow_zero=True))
    expected = project_decision_observation(basis, observation)
    if expected != (root, orders, unknown):
        raise ValueError('Decision observation differs from its exact parent or native codec')
    return observation


def qualified_observation_rows(unit, qualified, *, rekeyed_basis=None):
    """Legacy proofs lack observations; current native qualifiers retain them."""
    evidence = qualified._proof
    if evidence.observation is None:
        if evidence.observation_hash:
            raise ValueError('Decision observation proof is incomplete')
        return ()
    if observation_digest(evidence.observation) != evidence.observation_hash:
        raise ValueError('Decision observation changed after native qualification')
    basis = unit.basis if rekeyed_basis is None else rekeyed_basis
    from .rule_decision_basis_projection import rekey_rule_decision_basis
    expected, _ = rekey_rule_decision_basis(unit.basis, unit.intents, batch_id=basis['batch_id'])
    if basis != expected:
        raise ValueError('Decision observation rekeying changed its causal basis')
    root, orders, unknown = project_decision_observation(basis, evidence.observation)
    return tuple((table.name, rows) for table, rows in
        ((OBSERVATION, (root,)), (OBSERVED_ORDER, orders), (UNOBSERVED_ORDER, unknown)) if rows)


def verify_decision_observation_graph(families, bases):
    roots, orders, unknown = (tuple(families.get(table.name, ())) for table in
        (OBSERVATION, OBSERVED_ORDER, UNOBSERVED_ORDER))
    by_id = {basis['record_id']: basis for basis in bases}
    ids = {root['record_id'] for root in roots}
    if (len(ids) != len(roots) or not ids <= set(by_id)
            or any(row['parent_record_id'] not in ids for row in (*orders, *unknown))):
        raise ValueError('Decision observation graph has missing, duplicate or foreign roots')
    grouped = []
    for rows in (orders, unknown):
        if len({row['record_id'] for row in rows}) != len(rows):
            raise ValueError('Decision observation graph repeats child identities')
        index = {identity: [] for identity in ids}
        for row in rows:
            index[row['parent_record_id']].append(row)
        grouped.append(index)
    for root in roots:
        restore_decision_observation(by_id[root['record_id']], root,
            tuple(sorted(grouped[0][root['record_id']], key=lambda row: row['ordinal'])),
            tuple(sorted(grouped[1][root['record_id']], key=lambda row: row['ordinal'])))

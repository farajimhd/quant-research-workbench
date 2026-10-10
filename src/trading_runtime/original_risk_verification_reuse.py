"""Declared reuse limits and existing exclusive-writer frontier attestation.

This does not enable reuse or admit orders. The native verifier must separately
bind the declared policy and source before issuing an operation engine.
"""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from .journal_contract import canonical_json

RULE = 'bounded-original-risk-verification-reuse@1'
POLICY_KEY = 'original_risk_verification_reuse_policy'


@dataclass(frozen=True, slots=True)
class OriginalRiskVerificationReusePolicy:
    max_requests: int
    max_bytes: int

    def __post_init__(self):
        if (type(self.max_requests) is not int or not 1 <= self.max_requests <= 4096
                or type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 16777216):
            raise ValueError('Verification reuse requires exact bounded integer limits')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, max_requests=self.max_requests, max_bytes=self.max_bytes,
                    lifetime='one_unchanged_exclusive_writer_frontier_operation',
                    overflow='complete_cold_verification', failures='never_reusable')


def parse_original_risk_verification_reuse_policy(release, policies):
    count = release.rule_set_contracts.count(RULE)
    supplied = POLICY_KEY in policies
    if not count and not supplied:
        return None
    if count != 1 or not supplied or type(policies[POLICY_KEY]) is not dict:
        raise ValueError('Verification reuse needs one declared rule and exact policy')
    payload = policies[POLICY_KEY]
    try:
        result = OriginalRiskVerificationReusePolicy(payload['max_requests'], payload['max_bytes'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Verification reuse policy is incomplete') from exc
    if canonical_json(payload) != canonical_json(result.payload()):
        raise ValueError('Verification reuse semantics differ from the declared policy')
    return result


def exclusive_writer_frontier_fence(client, source):
    """Recheck exact native source, owner, dispatch gate and immutable commit.

A cold reader lacks this capability and must retain full verification. The
caller must not turn a failed check into an apparently valid reuse scope.
"""
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from .arte_typed_insert_dispatch import TypedInsertDispatch
    from .arte_journal_writer import _CONTRACTS, _literal, _rows

    if type(source) is not CertifiedPriceReadbackAuthority:
        raise ValueError('Verification reuse needs the exact certified price authority')
    source.__post_init__()
    lease = getattr(client, 'backtest_v4_lease', None)
    dispatch = getattr(client, 'typed_insert_dispatch', None)
    if (not isinstance(lease, BacktestV4KeeperLease) or lease.run_id != source.run_id
            or lease.epoch < 1 or getattr(client, 'typed_insert_strict', False) is not True
            or not isinstance(dispatch, TypedInsertDispatch)
            or dispatch.keeper is not lease.owner._session.client):
        raise RuntimeError('Verification reuse lacks its original exclusive writer')
    lease.assert_current()
    gate, _ = dispatch._read_gate(source.run_id)
    if (gate.mode != 'open' or gate.inflight or gate.registered
            or gate.active_batch_id != str(UUID(int=0)) or gate.compacted_through < 1):
        raise RuntimeError('Verification reuse requires a completely compacted open gate')
    columns = ','.join(name for name, _ in _CONTRACTS['trading_commit_v4'].columns)
    rows = _rows(client, f'SELECT {columns} FROM arte.trading_commit_v4 '
                 f'WHERE run_id={_literal(source.run_id)} '
                 f'AND batch_id=toUUID({_literal(gate.compacted_batch_id)}) '
                 'LIMIT 2 FORMAT JSONEachRow')
    if (len(rows) != 1
            or sha256(canonical_json(rows[0]).encode()).hexdigest() != gate.compacted_commit_hash
            or rows[0]['run_id'] != source.run_id
            or str(rows[0]['batch_id']) != gate.compacted_batch_id
            or rows[0]['last_sequence'] != gate.compacted_through
            or rows[0]['status'] != 'running'):
        raise RuntimeError('Verification reuse frontier differs from immutable commit metadata')
    lease.assert_current()
    current, _ = dispatch._read_gate(source.run_id)
    if current != gate:
        raise RuntimeError('Verification reuse gate changed while attesting its frontier')
    return dict(run_id=source.run_id, lease_identity=id(lease), epoch=lease.epoch,
                dispatch_identity=id(dispatch), client_identity=id(client),
                price_identity=id(source), plan_token=source.plan.token,
                source_token=source.plan.source.token,
                activity_identity=id(source.entry_activity_source),
                entry_cost_identity=id(source.entry_spread_risk_source),
                batch_id=gate.compacted_batch_id, through_sequence=gate.compacted_through,
                commit_hash=gate.compacted_commit_hash)


def selected_policy(client, source):
    """Installed typed declaration selects reuse; permissions cannot select it."""
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
    from .numbered_fixed_strategy import numbered_fixed_strategy
    from .original_risk_diagnostic_profile import validate_original_risk_profile
    if type(source) is not CertifiedPriceReadbackAuthority:
        return None
    activity = source.entry_activity_source
    number = getattr(activity, 'strategy_number', None)
    if type(number) is not int:
        return None
    contract = numbered_fixed_strategy(number)
    policy = getattr(contract, 'original_risk_verification_reuse_policy', None)
    if policy is None:
        return None
    source.__post_init__()
    actual = getattr(client, 'confirmed_original_risk_policy', None)
    if actual != contract.confirmed_original_risk_policy:
        raise ValueError('Verification reuse runner differs from the installed diagnostic contract')
    validate_original_risk_profile(getattr(client, 'automatic_ladder_profile', False),
        getattr(client, 'entry_spread_risk_profile', False), actual)
    return policy

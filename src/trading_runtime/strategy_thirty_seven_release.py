"""Prepared exact-parent Strategy37 contract; public registration stays closed."""
from copy import deepcopy

from . import strategy_thirty_six_release as parent_policy
from .strategy_episode_activity_veto import episode_activity_veto_policy_payload
from .strategy_registry import NumberedStrategyRelease

PARENT_REVISION_ID = 'strategy-one-36:6c026cd8-c2a6-4988-a12c-47e8224fa6b1'
PARENT_PAYLOAD_HASH = '628fd276a85aae4561624f6e311caa7385cb9c297954bb944a09e316262337d9'
INHERITED_POLICIES = deepcopy({**parent_policy.INHERITED_POLICIES,
    'entry_activity_policy': parent_policy.ENTRY_ACTIVITY_POLICY})
EPISODE_ACTIVITY_POLICY = episode_activity_veto_policy_payload()
BEHAVIOR = (
    'Strategy37 inherits exact pinned Strategy36 activation, original MACD1s '
    'episode and first-setup anchors, entry predicates, no-add policy, sizing, '
    'costs, V7 seeds, full RTH warmup for AH and all held-position management. '
    'A completed candidate that passes every inherited predicate except the '
    'fully observed entry-activity fade comparison vetoes subsequent new entries '
    'and reentries in the same original ticker episode. The triggering candidate '
    'is rejected. Later recovery of the rolling activity denominator cannot '
    'clear that veto. A new original episode resets the veto. Missing evidence '
    'rejects the current candidate under Strategy36 but never latches a failure; '
    'history before session activation does not latch. The veto is computed '
    'causally over the full certified candidate prefix before survivor pruning, '
    'including candidates observed while a position is held. It affects only '
    'future acquisitions and never forces or postpones an exit. Backtest only; '
    'no live or public resume admission.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_policy.release_contract()
    values = dict(number=37, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=37, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts + (EPISODE_ACTIVITY_POLICY['policy_id'],),
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def verify_exact_parent(source):
    """Validate the certified published parent without authorizing execution."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration
            or source.strategy_number != 36
            or source.revision()['revision_id'] != PARENT_REVISION_ID
            or source.payload_hash != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy37 requires exact pinned certified Strategy36')
    return parent_policy.verify_strategy_thirty_six_manifest(source.payload['strategy'])

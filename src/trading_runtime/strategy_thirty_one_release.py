"""Prepared immutable Strategy 31 declaration; executor admission stays closed."""
from .strategy_registry import NumberedStrategyRelease
from .strategy_thirty_release import release_contract as parent_release_contract
from .strategy_profit_giveback import POLICY_ID, profit_giveback_policy_payload

PARENT_REVISION_ID = 'strategy-one-30:35ea2e85-6e6b-4d08-a915-559b21afda6a'
PARENT_PAYLOAD_HASH = '198ff70b7acf3ff5651d6c2e2b2f17075298a102e45e0ecd417cc07cce0d7327'
PROFIT_PROTECTION_POLICY = {
    **profit_giveback_policy_payload(),
    'arming_checkpoint': 'one_native_complete_checkpoint_when_one_original_risk_is_reached',
    'arming_confirmation': 'durable_verified_snapshot_and_committed_market_cursor_before_exit',
    'later_high_reference': 'frozen_prior_arming_checkpoint_high',
    'witness_contract': 'trading_profit_giveback_v4',
    'parent_release_revision': PARENT_REVISION_ID,
    'parent_release_payload': PARENT_PAYLOAD_HASH,
}
BEHAVIOR = (
    'Strategy 31 inherits exact pinned Strategy 30 entries, original-risk '
    'failure exits, sizing, costs, targets and structural protection. A '
    'completed position high reaching one original ask-to-stop risk arms '
    'profit protection through a single native committed checkpoint. A later '
    'completed 5s close and fresh current bid at or below half that risk above '
    'the original ask, with MACD line below signal, proposes an urgent full '
    'position exit. Current-bucket arming cannot exit immediately. The exact '
    'prior checkpoint high, source entry and causal clocks are normalized '
    'witness facts. Missing evidence and pending exits do not create synthetic '
    'orders. V7 prior-day seeding and RTH warming remain inherited. Backtest '
    'only; live and public resume remain closed.'
)


def release_contract() -> NumberedStrategyRelease:
    """Sealed declaration, not an installed executor or published approval."""
    parent = parent_release_contract()
    values = dict(number=31, executor_strategy_id=parent.executor_strategy_id,
                  executor_revision=31, evaluation_interval=parent.evaluation_interval,
                  input_contracts=parent.input_contracts,
                  rule_set_contracts=(*parent.rule_set_contracts, POLICY_ID),
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def verify_installed_strategy_thirty_one_release(manifest):
    """Future preflight cannot treat a prepared declaration as installed."""
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    installed = numbered_strategy(31)
    fixed_strategy_executor(installed.executor_strategy_id, installed.executor_revision)
    expected = release_contract()
    if (installed != expected or manifest.get('contract') != expected.canonical_payload()
            or manifest.get('approved_digest') != expected.approved_digest
            or manifest.get('profit_protection_policy') != PROFIT_PROTECTION_POLICY
            or manifest.get('source_revision_id') != PARENT_REVISION_ID
            or manifest.get('source_payload_hash') != PARENT_PAYLOAD_HASH):
        raise ValueError('Strategy 31 published release differs from installed approval')
    return installed

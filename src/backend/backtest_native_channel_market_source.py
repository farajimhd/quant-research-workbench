"""Compile declared feature dependencies and pin their certified source plan."""
from .backtest_market_data import (
    CertifiedMarketDayPlan, certified_market_plan_from_arte,
    compile_required_resolutions, effective_execution_interval, project_market_day_plan,
)
from .backtest_native_channel_sequences import NativeChannelSequencePolicy


def native_channel_source_configuration(policy):
    if type(policy) is not NativeChannelSequencePolicy:
        raise ValueError('Native source dependencies require the exact declared sequence policy')
    policy.__post_init__()
    return {
        'strategy': {'execution_interval': str(policy.inputs.decision_interval_ms) + 'ms'},
        'feature_inputs': [{'timeframe': str(value) + 'ms'}
            for value in policy.inputs.resolutions_ms],
    }


def certify_native_channel_market_source(market, policy, *, exclusions=(),
        plan_factory=certified_market_plan_from_arte):
    """Keep source tokens separate while proving identical canonical stage units.

    The producer uses its feature dependencies, not the trading strategy's MACD
    dependencies. Both plans must certify the same complete original universe,
    build and immutable stage units. Only sealed external input exclusions may
    then project the feature source, matching producer projection semantics.
    """
    if (type(market) is not CertifiedMarketDayPlan or not market.tickers
            or type(exclusions) is not tuple
            or any(type(value) is not str or not value for value in exclusions)
            or exclusions != tuple(sorted(set(exclusions)))):
        raise ValueError('Native source requires a full certified market and exact external exclusions')
    configuration = native_channel_source_configuration(policy)
    source = plan_factory(sessions=market.sessions, tickers=market.tickers,
        configuration=configuration)
    if (type(source) is not CertifiedMarketDayPlan
            or (source.build_id, source.definition_hash, source.sessions,
                source.tickers, source.units) != (market.build_id,
                market.definition_hash, market.sessions, market.tickers, market.units)
            or source.execution_interval != effective_execution_interval(configuration)
            or source.required_resolutions_ms != compile_required_resolutions(
                configuration, source.execution_interval)):
        raise ValueError('Native feature plan differs from the certified trading source units or dependencies')
    selected = tuple(value for value in source.tickers if value not in exclusions)
    return project_market_day_plan(source, selected) if selected != source.tickers else source

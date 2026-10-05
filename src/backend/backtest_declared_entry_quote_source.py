"""Exact declared source-version selection shared by execution and cold proof."""
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy


def entry_spread_risk_authority_type(strategy_number):
    contract = numbered_fixed_strategy(strategy_number)
    source = contract.entry_spread_risk_quote_source_contract
    if source == 'declared-entry-spread-risk-quote-source@1':
        from .backtest_entry_spread_risk import EntrySpreadRiskReadbackAuthority
        return EntrySpreadRiskReadbackAuthority
    if source == 'declared-entry-spread-risk-quote-source@2':
        from .backtest_entry_spread_risk_v2 import EntrySpreadRiskV2ReadbackAuthority
        return EntrySpreadRiskV2ReadbackAuthority
    raise ValueError('Entry cost requires an exact installed quote-source contract')


def declared_entry_spread_risk_authority(run_id, plan, strategy_number):
    return entry_spread_risk_authority_type(strategy_number)(run_id, plan, strategy_number)


def load_declared_entry_spread_risk_plan(market, parent, policy, *, source_contract, client, batch_size=512):
    if source_contract == 'declared-entry-spread-risk-quote-source@1':
        from .backtest_entry_spread_risk import load_entry_spread_risk_plan as loader
    elif source_contract == 'declared-entry-spread-risk-quote-source@2':
        from .backtest_entry_spread_risk_v2 import load_entry_spread_risk_plan_v2 as loader
    else:
        raise ValueError('Entry cost quote-source contract is missing or unsupported')
    return loader(market, parent, policy, client=client, batch_size=batch_size)

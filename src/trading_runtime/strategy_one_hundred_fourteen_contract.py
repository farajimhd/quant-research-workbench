"""Typed declaration adapter, without source approval or publication."""
from .journal_contract import canonical_json
from .numbered_fixed_strategy import DeclaredFixedStrategyContract
from .strategy_one_hundred_fourteen_release import release_contract, declared_policies


def strategy_one_hundred_fourteen_contract():
    release = release_contract()
    return DeclaredFixedStrategyContract(release.number, release.executor_strategy_id,
        release.evaluation_interval, release, canonical_json(declared_policies()))

"""Exact issued-session binding for declared complete-response readers.

The native preparer owns this binding. Neither a caller policy nor a market
plan constructor can grant installed session or producer admission.
"""
from threading import RLock
from weakref import WeakKeyDictionary

from .backtest_fixed_structural_lot_execution import PreparedFixedStructuralLotSession
from .backtest_strategy_one_plan import StrategyOneFixedPlans
from .backtest_market_data import CertifiedMarketDayPlan, project_market_day_plan
from .backtest_liquidity_price import PriceLevelPlan, certify_price_level_plan
from .backtest_complete_market_source import prepared_complete_market_source
from src.trading_runtime.complete_market_window_policy import installed_complete_market_window_policy

_BINDINGS = WeakKeyDictionary()
_LOCK = RLock()


def _bind_complete_market_plans(session, *, plans, through_boundary_ms, client):
    if type(session) is not PreparedFixedStructuralLotSession or type(plans) is not StrategyOneFixedPlans:
        raise ValueError('Complete market reader needs the exact issued native session and plans')
    session.require(market=plans.market, candidates=plans.candidates, entry=plans.entry,
        through_boundary_ms=through_boundary_ms, run_id=session.operation.source.run_id,
        number=session.operation.source._revision)
    policy = installed_complete_market_window_policy(session.operation.source)
    if policy is None:
        raise ValueError('Complete market reader binding lacks an installed policy')
    if not plans.execution_market.tickers:
        raise ValueError('Empty native session must not bind a market reader')
    # The original complete producer verifier owns the price plan, including
    # its attempt identity and liquidity content. No caller-created price seal.
    actual = certify_price_level_plan(plans.execution_market, client)
    if actual != plans.prices:
        raise ValueError('Complete market reader differs from certified execution prices')
    # Scheduler projections originate from the whole certified market. A
    # projection of execution_market would introduce a different parent token.
    # The separately certified prices retain the admitted execution population.
    binding = session.operation.source, plans.market, plans.prices, through_boundary_ms, policy
    with _LOCK:
        previous = _BINDINGS.get(session)
        if previous is not None and previous != binding:
            raise ValueError('Complete market reader session binding changed')
        _BINDINGS[session] = binding


def installed_complete_market_source(session, plan, *, prices, through_boundary_ms, client_factory):
    if session is None:
        return None
    if type(session) is not PreparedFixedStructuralLotSession:
        raise ValueError('Complete market reader needs an exact native session')
    session._require_issued()
    source = session.operation.source
    policy = installed_complete_market_window_policy(source)
    if policy is None:
        return None
    with _LOCK:
        binding = _BINDINGS.get(session)
    if (binding is None or binding[0] is not source
            or type(plan) is not CertifiedMarketDayPlan or type(prices) is not PriceLevelPlan
            or type(through_boundary_ms) is not int or through_boundary_ms != binding[3]
            or policy != binding[4] or not plan.tickers
            or set(plan.tickers) - {unit.ticker for unit in binding[2].units}):
        raise ValueError('Complete market reader escaped its issued session binding')
    if (plan != project_market_day_plan(binding[1], plan.tickers)
            or prices != binding[2].projected(plan)):
        raise ValueError('Complete market reader uses another market or price authority')
    return prepared_complete_market_source(plan, prices=prices,
        through_boundary_ms=through_boundary_ms, client_factory=client_factory, policy=policy)

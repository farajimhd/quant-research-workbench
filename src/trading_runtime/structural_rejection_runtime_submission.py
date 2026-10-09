"""Fresh actor checks for an issued own exit; no direct order execution."""
from .profit_armed_structural_rejection_confirmation import (
    require_structural_rejection_confirmation, _ISSUED)
from weakref import WeakKeyDictionary
from contextlib import asynccontextmanager

_ACTIVE_DRAINS=WeakKeyDictionary()


@asynccontextmanager
async def runtime_structural_rejection_drain(runtime,confirmations):
    """Verify the complete batch before mutations; permit only ordered exits."""
    if (runtime in _ACTIVE_DRAINS or type(confirmations) is not tuple
            or not confirmations or len(confirmations)>65536
            or len({id(item) for item in confirmations})!=len(confirmations)):
        raise ValueError('Structural rejection drain requires unique bounded confirmations')
    first=_ISSUED[require_structural_rejection_confirmation(confirmations[0],runtime=runtime)]
    for confirmation in confirmations:
        binding=_ISSUED[require_structural_rejection_confirmation(confirmation,runtime=runtime)]
        if binding[2] is not first[2] or binding[3] is not first[3]:
            raise ValueError('Structural rejection drain spans different published financial inventories')
        await require_runtime_structural_rejection_exit(runtime,confirmation,full_capture=True)
    _ACTIVE_DRAINS[runtime]=[confirmations,0]
    try:
        yield
        if _ACTIVE_DRAINS[runtime][1]!=len(confirmations):
            raise ValueError('Structural rejection drain did not complete every accepted exit')
    finally:
        _ACTIVE_DRAINS.pop(runtime,None)


def complete_runtime_structural_rejection_exit(runtime,confirmation):
    active=_ACTIVE_DRAINS.get(runtime)
    if active is None or active[1]>=len(active[0]) or active[0][active[1]] is not confirmation:
        raise ValueError('Structural rejection completion differs from ordered active drain')
    active[1]+=1


async def require_runtime_structural_rejection_exit(runtime, confirmation, *,
                                                   full_capture=False):
    from .runtime import TradingRuntime, RunMode
    from .strategy_engine import StrategyAssignment, AssignmentStatus
    from .simulated_broker import SimulatedBrokerAdapter
    from .order_management import OrderManagementEngine
    from .portfolio import PortfolioManagementEngine
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_view
    from src.backend.backtest_management_structural_guard import (
        capture_management_structural_guard, require_management_structural_guard)
    from .profit_armed_structural_rejection_financial_checkpoint import issue_financial_capture
    require_structural_rejection_confirmation(confirmation, runtime=runtime)
    active=_ACTIVE_DRAINS.get(runtime)
    if active is not None and (active[1]>=len(active[0]) or active[0][active[1]] is not confirmation):
        raise ValueError('Structural rejection submission differs from ordered active drain')
    owner, request, publication, capture, *_ = _ISSUED[confirmation]
    if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
            or type(runtime.broker) is not SimulatedBrokerAdapter
            or type(runtime.order_manager) is not OrderManagementEngine
            or type(runtime.portfolio) is not PortfolioManagementEngine
            or type(runtime.journal) is not BacktestMemoryJournal
            or request.financial.account_id not in runtime.config.account_ids):
        raise ValueError('Structural rejection submission lacks actual Backtest financial actors')

    def assignment():
        rows = tuple(row for row in runtime.strategy.assignments()
                     if row.assignment_id == request.financial.assignment_id)
        if (len(rows) != 1 or type(rows[0]) is not StrategyAssignment
                or rows[0].account_id != request.financial.account_id
                or rows[0].ticker != request.financial.ticker
                or rows[0].strategy_id != runtime.config.strategy_id
                or rows[0].strategy_revision != runtime.config.strategy_revision):
            raise ValueError('Structural rejection submission lost its current assignment')
        return rows[0]

    selected = assignment()
    expected = capture_management_structural_guard(request.financial)
    current = await read_strategy_one_financial_view(selected, runtime.broker, runtime.order_manager)
    require_structural_rejection_confirmation(confirmation, runtime=runtime)
    require_management_structural_guard(expected, current)
    if (assignment() is not selected
            or current.status is not AssignmentStatus.MANAGING
            or current.permissions.exit is not True or current.position_quantity <= 0
            or current.pending_entry or current.pending_exit or current.pending_capital_request):
        raise ValueError('Structural rejection financial truth changed after confirmation')
    if full_capture and active is None:
        actual = issue_financial_capture(publication.profile, capture.state,
                                        sequence=capture.sequence)
        if actual.image_json != capture.image_json:
            raise ValueError('Structural rejection complete actor inventory changed before admission')
    return current

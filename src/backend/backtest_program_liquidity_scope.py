"""Project certified broker reads without changing programme decision coverage."""
import polars as pl
from datetime import datetime, timezone


def program_cohort_liquidity_tickers(actor, manager, projection, *, first_clock, last_clock, broker_orders=()):
    """Read ranked candidates and conservatively retain all prior lifecycle work.

    Full assignment/feature/decision coverage remains owned by the existing
    cohort. Future ranks within this prepared cohort only plan transport; bars
    still reach the actual broker chronologically. No program receives this
    projection as a feature or uses it to infer fills.
    """
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    from src.trading_runtime.order_management import OrderManagementEngine
    from src.trading_runtime.ibkr_schema import LiveOrder
    from .backtest_program_inputs import PreparedProgramScannerProjection
    if (type(actor) is not AssignedProgramStrategy or type(manager) is not OrderManagementEngine
            or type(projection) is not PreparedProgramScannerProjection
            or projection.release_hash != actor.program_release.header['content_hash']
            or type(broker_orders) is not tuple or any(type(order) is not LiveOrder for order in broker_orders)
            or any(not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None
                for clock in (first_clock, last_clock))
            or first_clock > last_clock):
        raise ValueError('Programme liquidity projection lacks native owned scope')
    assignments = actor._by_assignment
    assigned = {row.ticker for row in assignments.values()}
    clock_dtype = projection.frame.schema['boundary_at']
    selected = set(projection.frame.filter(pl.col('boundary_at').is_between(
        pl.lit(first_clock.astimezone(timezone.utc)).cast(clock_dtype),
        pl.lit(last_clock.astimezone(timezone.utc)).cast(clock_dtype)))
        .get_column('ticker').unique().to_list())
    if not selected <= assigned:
        raise ValueError('Scanner liquidity projection contains a foreign assignment')
    retained = set(actor._selection_clocks)
    for account, strategy, identity, listing in actor.execution_state.positions:
        assignment = assignments.get(identity)
        if (assignment is None or assignment.account_id != account or strategy != actor.strategy_id
                or actor._listings[identity] != listing):
            raise ValueError('Programme execution state contains a foreign liquidity owner')
        retained.add(identity)
    # Keep even terminal indexed groups conservatively. Their assignment may
    # own deferred capital, processed child orders or remaining lifecycle work.
    for account, identity in manager._groups_by_assignment:
        assignment = assignments.get(identity)
        if assignment is None or assignment.account_id != account:
            raise ValueError('Programme OMS contains a foreign liquidity owner')
        retained.add(identity)
    if not retained <= set(assignments):
        raise ValueError('Programme lifecycle contains an unknown liquidity assignment')
    selected.update(assignments[identity].ticker for identity in retained)
    accounts_by_ticker = {(row.account_id, row.ticker) for row in assignments.values()}
    for order in broker_orders:
        if (order.account, order.ticker) not in accounts_by_ticker:
            raise ValueError('Programme broker contains a foreign liquidity owner')
        selected.add(order.ticker)
    return tuple(sorted(selected))

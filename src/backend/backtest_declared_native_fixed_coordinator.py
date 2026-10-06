"""Sequential declared proposal orchestration over the shared causal scheduler.

Prepared source objects are necessary evidence, never installed actor authority.
The caller must supply the independently installed source/Portfolio/OMS binding
before a proposal callback may reserve capital or submit an order.
"""
from collections.abc import Mapping
from time import perf_counter

import numpy as np

from .backtest_declared_base_entry_gate import DeclaredBaseEntryPolicy
from .backtest_declared_native_fixed_entry import DeclaredEntryPreparation
from .backtest_strategy_one_coordinator import StrategyOneProposalCounts
from .backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler, run_strategy_one_boundaries
from .backtest_strategy_one_static_gate import StrategyOneStaticGate
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.declared_native_entry_source import DeclaredNativeEntrySourcePolicy, parse_declared_native_entry_source


def declared_decision_gate(source, *, through_boundary_ms, start_after_boundary_ms=0):
    """Exact admitted scheduler keys; no rejected mask is assigned a false reason."""
    from .backtest_declared_native_fixed_plan import DeclaredEntrySourcePlan
    if type(source) is not DeclaredEntrySourcePlan:
        raise ValueError("Declared coordinator requires exact prepared source")
    windows, _ = DeclaredBaseEntryPolicy(source.parent.capabilities).parameters()
    resolution = int(source.parent.capabilities.execution_interval[:-2])
    if (type(through_boundary_ms) is not int or type(start_after_boundary_ms) is not int
            or not 0 <= start_after_boundary_ms <= through_boundary_ms <= max(w[2] for w in windows)
            or through_boundary_ms <= 0 or through_boundary_ms % resolution
            or start_after_boundary_ms % resolution):
        raise ValueError("Declared coordinator horizon differs from completed source clock")
    boundaries = np.asarray([f.boundary_ms for f in source.parent.base.facts], dtype=np.int64)
    selected = source.eligible_mask & (boundaries > start_after_boundary_ms) & (boundaries <= through_boundary_ms)
    facts = tuple(source.parent.base.facts[int(i)] for i in np.flatnonzero(selected))
    return StrategyOneStaticGate(facts, np.zeros(len(facts), dtype=np.uint8), np.arange(len(facts), dtype=np.int64))


def _preparations(values):
    if not isinstance(values, Mapping) or not values:
        raise ValueError("Declared coordinator needs an exact account/assignment roster")
    roster = dict(values)
    source = run = None
    for key, prep in roster.items():
        if (type(key) is not tuple or len(key) != 3 or type(prep) is not DeclaredEntryPreparation
                or type(key[0]) is not str or not key[0] or key[0] != key[0].upper()
                or key[1:] != (prep.account_id, prep.assignment_id)):
            raise ValueError("Declared preparation differs from account/assignment roster")
        prep.__post_init__()
        if source is None:
            source, run = prep.source, prep.run_id
        elif prep.source is not source or prep.run_id != run:
            raise ValueError("Declared roster has foreign run or source authority")
    tickers = {unit.ticker for unit in source.parent.market.units}
    if any(key[0] not in tickers for key in roster):
        raise ValueError("Declared assignment ticker is outside fenced source population")
    grouped = {}
    for (ticker, account, assignment), prep in roster.items():
        grouped.setdefault(ticker, {})[(account, assignment)] = prep
    return grouped, source


def _cursor_index(source):
    from .backtest_strategy_one_preparation import iter_strategy_one_entries
    prepared = source.parent.candidates.prepared
    for row in prepared:
        n = len(row.boundary_ms)
        for name in ("row_index", "boundary_ms", "episode_start_ms", "macd_boundary_ms",
                     "stop_bar_boundary_ms", "stop_low_int"):
            column = getattr(row, name)
            shape = (n, 4) if name == "macd_boundary_ms" else (n,)
            if type(column) is not np.ndarray or column.shape != shape or column.dtype.kind not in ("i", "u"):
                raise ValueError("Declared source cursor columns require exact integer arrays")
    cursors = tuple(iter_strategy_one_entries(prepared))
    indexed = {(c.ticker, c.boundary_ms): c for c in cursors}
    if len(indexed) != len(cursors):
        raise ValueError("Declared source cursor keys are duplicated")
    return indexed


def _candidate_matches(source, candidate, ticker, boundary, cursor_index):
    from .backtest_strategy_one_preparation import StrategyOneEntryCursor
    row, cursor = candidate.market_row, candidate.evidence
    expected_cursor = cursor_index.get((ticker, boundary))
    if (type(cursor) is not StrategyOneEntryCursor or expected_cursor is None
            or any(type(getattr(cursor, name)) is not int for name in ("boundary_ms", "source_row_index",
                "episode_start_ms", "stop_bar_boundary_ms", "stop_low_int"))
            or type(cursor.macd_boundary_ms) is not tuple
            or any(type(c) is not int for c in cursor.macd_boundary_ms)
            or cursor != expected_cursor):
        raise ValueError("Declared scheduler cursor differs from certified source columns")
    index = source.parent.index(ticker, boundary)
    fact = source.parent.base.facts[index]
    expected = dict(session_date=source.parent.market.sessions[0], ticker=ticker,
                    boundary_ms=boundary, resolution_ms=int(source.parent.capabilities.execution_interval[:-2]),
                    price_valid=1, quote_valid=int(source.quote_columns[3][index]),
                    bid_int=int(source.quote_columns[0][index]), ask_int=int(source.quote_columns[1][index]),
                    quote_timestamp_us=int(source.quote_columns[2][index]))
    if (any(type(row.get(name)) is not type(value) or row.get(name) != value for name, value in expected.items())
            or cursor.ticker != ticker or type(cursor.boundary_ms) is not int or cursor.boundary_ms != boundary
            or type(cursor.episode_start_ms) is not int or cursor.episode_start_ms != fact.episode_start_ms):
        raise ValueError("Declared scheduler candidate differs from independently prepared source")


async def run_declared_native_fixed_proposals(
    scheduler, preparations, *, source_policy, through_boundary_ms, start_after_boundary_ms=0,
    process_broker_boundary, financial_views, on_entry_proposal, on_management,
    position_source_owned, financially_active_tickers, finish_boundary,
    observe_activation, observe_completed_seconds, before_boundary=None,
    reentry_witness=None, stage_time=None,
):
    """Use original broker-first ordering and refresh shared cash after every action.

    Scheduler candidates must already be the complete vectorized admitted key
    subset in the declared horizon. The common scheduler rejects omitted,
    duplicate or extra keys. Active financial rows retain management callbacks.
    All source rejection detail remains in the original prepared plans.
    """
    if type(source_policy) is not DeclaredNativeEntrySourcePolicy:
        raise ValueError("Declared coordinator requires explicit typed source policy")
    source_policy.__post_init__()
    parse_declared_native_entry_source(source_policy.payload())
    roster, source = _preparations(preparations)
    if (type(scheduler) is not StrategyOneBoundaryScheduler
            or scheduler.session_date != source.parent.market.sessions[0]
            or any(not callable(c) for c in (process_broker_boundary, financial_views,
                on_entry_proposal, on_management, position_source_owned, financially_active_tickers,
                finish_boundary, observe_activation, observe_completed_seconds))
            or any(c is not None and not callable(c) for c in (before_boundary, reentry_witness, stage_time))):
        raise ValueError("Declared coordinator lacks exact scheduler callbacks/session")
    gate = declared_decision_gate(source, through_boundary_ms=through_boundary_ms,
                                  start_after_boundary_ms=start_after_boundary_ms)
    if {fact.ticker for fact in gate.facts} - set(roster):
        raise ValueError("Declared decision ticker lacks prepared assignments")
    cursor_index = _cursor_index(source)
    admitted_keys = {(fact.ticker, fact.boundary_ms) for fact in gate.facts}
    candidates = proposals = management = 0

    async def timed(stage, operation):
        if stage_time is None:
            return await operation
        started = perf_counter()
        try:
            return await operation
        finally:
            stage_time(stage, started)

    async def evaluate(ticker, resolutions, candidate):
        nonlocal candidates, proposals, management
        clocks = {row.get("boundary_ms") for row in resolutions.values()}
        if len(clocks) != 1 or any(type(clock) is not int for clock in clocks):
            raise ValueError("Declared evaluation has mismatched completed clocks")
        boundary = next(iter(clocks))
        ticker_roster = roster.get(ticker)
        if ticker_roster is None:
            raise ValueError("Declared active ticker lacks prepared assignments")
        if candidate is not None:
            _candidate_matches(source, candidate, ticker, boundary, cursor_index)

        async def current_views():
            views = await financial_views(ticker, boundary)
            if (type(views) is not tuple or not views
                    or any(type(v) is not StrategyOneFinancialView or v.ticker != ticker for v in views)):
                raise ValueError("Declared ticker lacks exact financial views")
            indexed = {(v.account_id, v.assignment_id): v for v in views}
            if len(indexed) != len(views) or set(indexed) != set(ticker_roster):
                raise ValueError("Declared financial roster differs from prepared assignments")
            return indexed

        current = await timed("declared_financial_views", current_views())
        ordered = tuple(sorted(current))
        for identity in ordered:
            view = current[identity]
            action = False
            active = (view.position_quantity > 0 or view.pending_entry or view.pending_exit
                      or view.pending_capital_request or ticker in scheduler.active_tickers)
            if candidate is None:
                if active:
                    management += 1
                    await timed("declared_management", on_management(view, resolutions, boundary))
                    action = True
            else:
                owned = position_source_owned(view)
                if type(owned) is not bool:
                    raise TypeError("Declared source ownership must be boolean")
                if owned:
                    # Retire even a now-flat owned source before any same-bucket reentry.
                    management += 1
                    await timed("declared_management", on_management(view, resolutions, boundary))
                    action = True
                else:
                    reentry = (await timed("declared_reentry", reentry_witness(view, candidate))
                               if view.completed_entries and reentry_witness is not None else None)
                    decision = ticker_roster[identity].propose(ticker, boundary, view, reentry=reentry)
                    candidates += 1
                    if decision.proposal is not None:
                        proposals += 1
                        await timed("declared_entry_proposal", on_entry_proposal(decision.proposal))
                        action = True
                    elif active:
                        management += 1
                        await timed("declared_management", on_management(view, resolutions, boundary))
                        action = True
            if action and identity != ordered[-1]:
                # Refresh all accounts after the actual callback has completed;
                # the callback's exit request alone never means cash was released.
                current = await timed("declared_financial_views", current_views())

    def validate_candidates(work):
        # Broker rows contain the candidate quote. Validate before any caller
        # callback can consume it or alter cash, orders or observations.
        for candidate in work.candidate_rows:
            ticker = candidate.market_row.get("ticker")
            if (ticker, work.boundary_ms) not in admitted_keys:
                raise ValueError("Declared scheduler candidate is outside admitted source keys")
            _candidate_matches(source, candidate, ticker, work.boundary_ms, cursor_index)

    async def validate_before_boundary(work):
        validate_candidates(work)
        if before_boundary is not None:
            await before_boundary(work)
            # Shared scheduler rows are mappings. Control callbacks must not
            # change the source fields later consumed by the broker.
            validate_candidates(work)

    completed = await run_strategy_one_boundaries(scheduler,
        before_boundary=validate_before_boundary, process_broker_boundary=process_broker_boundary,
        evaluate_ticker=evaluate, financially_active_tickers=financially_active_tickers,
        finish_boundary=finish_boundary, observe_activation=observe_activation,
        observe_completed_seconds=observe_completed_seconds, static_gate=gate, stage_time=stage_time)
    return StrategyOneProposalCounts(completed, candidates, proposals, management)

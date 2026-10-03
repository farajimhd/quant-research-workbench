"""Causal Strategy 44 session over native Portfolio/OMS and 100ms liquidity.

Market reads contain only admission witnesses and financially active tickers.
One bounded 60-second cache per active listing replaces per-tick queries.
"""
import asyncio
from collections import defaultdict
from contextlib import closing
from math import nan

from .backtest_market_data import iter_market_day_rows, project_market_day_plan, market_day_boundary
from .backtest_strategy_forty_four_facts import load_admission_facts, validate_history_parent
from .backtest_strategy_forty_four_native import StrategyFortyFourNativePort
from src.trading_runtime.strategy_forty_four_coordinator import StrategyFortyFourCoordinator


async def run_session(*, plan, history, prices, runtime, port, client_factory,
                      before_boundary, finish_boundary, read_window_ms=60_000):
    validate_history_parent(plan, history)
    if (type(port) is not StrategyFortyFourNativePort or port.runtime is not runtime
            or port.source_token != history.token or port.session_end_ms != plan.session_end_ms
            or type(read_window_ms) is not int or not 1000 <= read_window_ms <= 60_000
            or read_window_ms % 1000):
        raise ValueError("Strategy 44 session lacks its exact native and bounded source authorities")
    day = plan.market.sessions[0]
    assignments = {row.ticker: row.assignment_id for row in runtime.strategy.assignments()}
    if tuple(sorted(assignments)) != plan.tickers or len(runtime.config.account_ids) != 1:
        raise ValueError("Strategy 44 comparison needs one native account and its complete squeeze assignments")
    coordinator = StrategyFortyFourCoordinator(account_id=runtime.config.account_ids[0],
        session_date=runtime.config.anchor_date, source_token=history.token, port=port)
    admission_rows = []
    def read_admissions():
        with closing(client_factory()) as reader:
            return load_admission_facts(plan, history, reader, price_plan=prices,
                                       completed_market_rows=admission_rows)
    admissions = await asyncio.to_thread(read_admissions)
    facts_by_second, candidate_rows = defaultdict(list), defaultdict(dict)
    for facts in admissions:
        facts_by_second[facts.boundary_ms].append(facts)
    for row in admission_rows:
        candidate_rows[int(row["boundary_ms"])][row["ticker"]] = row
    del admission_rows
    history_by_ticker, caches, covered_until = {}, {}, {}
    def load_window(ticker, after, through):
        with closing(client_factory()) as reader:
            market = project_market_day_plan(plan.market, (ticker,))
            return {int(row["boundary_ms"]): row for row in iter_market_day_rows(market, reader,
                after_boundary_ms=after, through_boundary_ms=through,
                price_plan=prices.projected(market)) if int(row["resolution_ms"]) == 100}
    def read_history(ticker):
        with closing(client_factory()) as reader:
            return history.load_active_history(ticker, reader)
    for second in range(1000, plan.session_end_ms + 1, 1000):
        for boundary in range(second - 900, second + 1, 100):
            at = market_day_boundary(day, boundary)
            await before_boundary(boundary, at)
            rows = candidate_rows.pop(boundary, {})
            active = runtime.broker.financially_active_tickers()
            for ticker in sorted(active):
                if covered_until.get(ticker, 0) < boundary:
                    through = min(plan.session_end_ms, boundary - 100 + read_window_ms)
                    caches[ticker] = await asyncio.to_thread(load_window, ticker, boundary - 100, through)
                    covered_until[ticker] = through
                row = caches[ticker].pop(boundary, None)
                if row is not None:
                    if ticker in rows and rows[ticker] != row:
                        raise RuntimeError("Strategy 44 admission and active market witnesses differ")
                    rows[ticker] = row
            # Expiry is a causal timer, including intervals without events.
            await runtime.order_manager.expire_entry_deadlines(at)
            if rows:
                ordered = tuple(rows[ticker] for ticker in sorted(rows))
                await runtime.process_liquidity_boundary(ordered, at=at)
            runtime.last_event_time = at
            if boundary % 1000:
                continue
            for ticker, state in tuple(coordinator.batches.items()):
                if state.batch.facts.boundary_ms >= boundary or ticker not in runtime.broker.financially_active_tickers():
                    continue
                if ticker not in history_by_ticker:
                    history_by_ticker[ticker] = await asyncio.to_thread(read_history, ticker)
                feature = history_by_ticker[ticker][boundary // 1000 - 1]
                quote = runtime.broker.completed_liquidity_quote(ticker)
                valid = quote is not None and quote.bid_price > 0 and quote.ask_price >= quote.bid_price
                # QuoteEvent.ts is source time; never refresh carried quotes.
                delta = at - quote.ts if quote else None
                age = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) if delta is not None else 2_000_000
                leg_facts = await port.completed_leg_facts(state=state, feature=feature,
                    bid=quote.bid_price if valid else nan, quote_valid=valid and 0 <= age <= 1_000_000,
                    quote_age_us=max(0, age))
                await coordinator.manage(ticker, leg_facts)
                await port.cancel_invalidated_batch(state=state, feature=feature, at=at)
            if boundary == plan.session_end_ms - 300_000:
                await port.cancel_session_acquisitions(at=at)
            if boundary >= plan.session_end_ms - 60_000:
                for ticker, state in tuple(coordinator.batches.items()):
                    assignment = next(row for row in runtime.strategy.assignments() if row.ticker == ticker)
                    if runtime.broker.position_quantity(runtime.config.account_ids[0], assignment.conid, ticker) == 0:
                        continue
                    quote = runtime.broker.completed_liquidity_quote(ticker)
                    if quote is None or quote.bid_price <= 0 or not 0 <= (at - quote.ts).total_seconds() <= 1:
                        continue  # Defer to the next completed fresh quote; never fabricate fills.
                    for leg in state.legs:
                        if leg.group_id and leg.group_id not in port._leg_liquidations:
                            pointer = type("CutoffFact", (), dict(ticker=ticker, boundary_ms=boundary,
                                source_token=history.token))()
                            await port.liquidate_leg(state=state, ordinal=leg.ordinal,
                                bid=quote.bid_price, source_fact_id=history.fact_id(pointer))
            current = facts_by_second.pop(boundary, ())
            if current:
                await coordinator.decide(current, assignment_ids=assignments)
            await finish_boundary(boundary, at, coordinator)
            await asyncio.sleep(0)
    receipt = await port.terminal_receipt()
    return receipt

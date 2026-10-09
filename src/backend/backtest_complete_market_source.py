"""Prepared scheduler adapter; declaration and market shape are not admission.

The installed caller must independently verify the exact selected release,
factory, source inventory and producer plans before selecting this source.
No existing release or scheduler construction switches to it implicitly.
"""
from contextlib import closing

from src.backend.backtest_complete_market_response import CompleteMarketResponseBounds
from src.backend.backtest_complete_market_window import iter_complete_market_windows
from src.backend.backtest_market_data import CertifiedMarketDayPlan, project_market_day_plan
from src.backend.backtest_liquidity_price import PriceLevelPlan
from src.trading_runtime.complete_market_window_policy import CompleteMarketWindowPolicy


def prepared_complete_market_source(plan, *, prices, through_boundary_ms,
                                    client_factory, policy):
    if (type(plan) is not CertifiedMarketDayPlan or len(plan.sessions) != 1
            or plan.execution_interval.milliseconds != 100
            or type(prices) is not PriceLevelPlan
            or prices.source_build_id != plan.build_id
            or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms <= 57600000 or through_boundary_ms % 100
            or not callable(client_factory) or type(policy) is not CompleteMarketWindowPolicy):
        raise ValueError('Prepared complete source needs exact declared native scope')
    policy.__post_init__()
    bounds = CompleteMarketResponseBounds(policy.max_wire_bytes,
                                         policy.max_response_rows, policy.max_resident_bytes)

    def rows(ticker, after_boundary_ms):
        if (type(ticker) is not str or ticker not in plan.tickers
                or type(after_boundary_ms) is not int
                or not 0 <= after_boundary_ms <= through_boundary_ms
                or after_boundary_ms % 100):
            raise ValueError('Prepared complete source request escaped certified scope')
        if after_boundary_ms == through_boundary_ms:
            return
        scoped = project_market_day_plan(plan, (ticker,))
        selected_prices = prices.projected(scoped)
        reader = client_factory()
        if reader is None or not callable(getattr(reader, 'close', None)):
            raise ValueError('Prepared complete source needs a closable read client')
        with closing(reader):
            yield from iter_complete_market_windows(scoped, prices=selected_prices,
                after_boundary_ms=after_boundary_ms, through_boundary_ms=through_boundary_ms,
                window_span_ms=policy.window_span_ms, client=reader,
                response_bounds=bounds, max_window_rows=policy.max_window_rows)

    def source(ticker, after_boundary_ms):
        # Preserve the scheduler's existing may-block detection and bounded
        # refill behavior; all network responses close before rows are yielded.
        from src.backend.backtest_strategy_one_scheduler import _BufferedMarketIterator
        return _BufferedMarketIterator(rows(ticker, after_boundary_ms),
                                       batch_size=policy.read_ahead_groups)

    return source

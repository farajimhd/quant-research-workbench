"""Join certified completed admission sources, without deriving indicators."""
from math import nan

from .backtest_market_data import iter_market_day_rows, project_market_day_plan, market_day_boundary
from .backtest_market_data import _literal, SESSION_OPEN_OFFSET_MS
import json
from .backtest_strategy_forty_five_plan import FortyFiveSourcePlan
from .backtest_strategy_forty_five_source import CertifiedFortyFiveHistory
from src.trading_runtime.strategy_forty_five_rules import EntryFacts, ResistanceFact


def validate_history_parent(plan, history):
    if (type(plan) is not FortyFiveSourcePlan or type(history) is not CertifiedFortyFiveHistory
            or (history.market_token, history.identity_token, history.structure_token)
                != (plan.market.token, plan.identity.token, plan.structure.token)
            or tuple(row["ticker"] for row in history.populations) != plan.tickers):
        raise ValueError("Strategy 45 history changed its independently certified source plan")
    for row in history.populations:
        ticker = row["ticker"]
        if (row["admission_ms"] != plan.admissions[ticker]
                or any(row[name] != plan.listings[ticker][name] for name in ("symbol_id", "listing_id", "security_id"))):
            raise RuntimeError("Strategy 45 population differs from its certified first signal or listing")


def load_admission_facts(plan, history, reader, *, batch_size=32, price_plan=None, completed_market_rows=None):
    """Read at most 32 candidates' ten completed 100ms buckets per query.

    The decision quote matches the research one-second argMaxIf snapshot. The
    execution VWAP is the existing producer's cumulative eligible VWAP column.
    Native broker fills continue to consume each individual 100ms bucket.
    """
    validate_history_parent(plan, history)
    if type(batch_size) is not int or not 1 <= batch_size <= 64:
        raise ValueError("Strategy 45 admission read batch must be bounded")
    if completed_market_rows is not None and (type(completed_market_rows) is not list or price_plan is None):
        raise ValueError("Strategy 45 broker witnesses require a price-certified bounded collector")
    result = []
    day = plan.market.sessions[0]
    anchor = market_day_boundary(day, 0)
    # Exact epoch arithmetic; float timestamps cannot establish quote age.
    from datetime import datetime, timezone
    delta = anchor - datetime(1970, 1, 1, tzinfo=timezone.utc)
    origin_us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    for offset in range(0, len(plan.tickers), batch_size):
        names = plan.tickers[offset:offset + batch_size]
        market = project_market_day_plan(plan.market, names)
        boundaries = {ticker: tuple(range(plan.admissions[ticker] - 900, plan.admissions[ticker] + 1, 100))
                      for ticker in names}
        rows = list(iter_market_day_rows(market, reader, through_boundary_ms=plan.session_end_ms,
            candidate_boundaries=boundaries, price_plan=price_plan.projected(market) if price_plan else None))
        # The native sparse reader intentionally emits only 100ms rows. Read
        # the certified completed-second trade count explicitly; do not infer
        # a missing second or use a future bar.
        units = {row.ticker: row for row in market.units if row.stage == "bars"}
        keys = ",".join(f"({_literal(ticker)},toUUID({_literal(units[ticker].attempt_id)}),"
            f"{(plan.admissions[ticker] + SESSION_OPEN_OFFSET_MS) // 1000 - 1})" for ticker in names)
        seconds_by_ticker = {}
        query = ("SELECT ticker,trade_count FROM arte.bars_v1 "
            f"WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(day)}) "
            f"AND resolution_ms=1000 AND (ticker,attempt_id,bucket_index) IN ({keys}) FORMAT JSONEachRow")
        for line in reader.execute(query).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["ticker"] in seconds_by_ticker or row["ticker"] not in names:
                raise RuntimeError("Strategy 45 completed-second trade count has duplicate or foreign keys")
            seconds_by_ticker[row["ticker"]] = int(row["trade_count"])
        if completed_market_rows is not None:
            completed_market_rows.extend(row for row in rows if int(row["resolution_ms"]) == 100)
        for ticker in names:
            admission = plan.admissions[ticker]
            feature = history.feature(ticker, admission)
            hundred = sorted((row for row in rows if row["ticker"] == ticker
                              and int(row["resolution_ms"]) == 100), key=lambda row: int(row["boundary_ms"]))
            if len({int(row["boundary_ms"]) for row in hundred}) != len(hundred):
                raise RuntimeError("Strategy 45 admission market sources contain duplicate keys")
            quotes = [row for row in hundred if int(row["quote_valid"]) == 1
                and 0 < int(row["bid_int"]) <= int(row["ask_int"])
                and 0 < int(row["quote_timestamp_us"]) <= int(row["last_event_us"])
                < origin_us + int(row["boundary_ms"]) * 1000]
            quote = quotes[-1] if quotes else None
            stamp = int(quote["quote_timestamp_us"]) if quote else 0
            age = origin_us + admission * 1000 - stamp if stamp else 2_000_000
            latest = hundred[-1] if hundred else None
            levels = plan.structure.levels(ticker, boundary_ms=admission)
            def number(name):
                return float(feature[name]) if feature[name] is not None else nan
            fact = EntryFacts(ticker=ticker, boundary_ms=admission, admission_ms=admission,
                session_end_ms=plan.session_end_ms, observed=bool(feature["observed"]),
                quote_valid=quote is not None and 0 <= age <= 1_000_000, quote_age_us=max(0, age),
                bid=int(quote["bid_int"]) / 10_000 if quote else nan,
                ask=int(quote["ask_int"]) / 10_000 if quote else nan,
                close=number("close"), low=number("low"), high=number("high"),
                dollar_volume=number("dollar_volume"), trades=seconds_by_ticker.get(ticker, 0),
                volume=sum(float(row["execution_volume"]) for row in hundred),
                vwap=float(latest["execution_vwap"]) if latest and float(latest["execution_vwap"]) > 0 else nan,
                previous_five_second_close=number("previous_five_second_close"),
                previous_ten_second_mean_notional=number("previous_ten_second_mean_notional"),
                swing_low=number("swing_low"), swing_available_ms=int(feature["swing_available_ms"]),
                structural_boundary_ms=admission,
                resistances=tuple(ResistanceFact(str(row["unified_level_id"]), float(row["lower"]))
                    for row in levels if row["role"] == "resistance"), source_token=history.token, tradable=True,
                liquidity=history.liquidity_book.fact(ticker, admission))
            fact.validate()
            result.append(fact)
    return tuple(sorted(result, key=lambda row: (row.boundary_ms, row.ticker)))

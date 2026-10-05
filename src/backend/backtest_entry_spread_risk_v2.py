"""Quote-source@2: qualified UUID predicate and distinct output alias.

The original plan, reduction, and witness contracts remain unchanged.
"""
from dataclasses import dataclass
import json
import numpy as np
from .backtest_market_data import CertifiedMarketDayPlan, verify_market_day_plan, _literal
from .backtest_strategy_episode_activity_gate import EpisodeActivityStaticGate
from .backtest_entry_spread_risk import CertifiedEntrySpreadRiskPlan, EntrySpreadRiskReadbackAuthority, _token
from src.trading_runtime.entry_spread_risk import EntrySpreadRiskPolicy, canonical_price_int

QUOTE_SOURCE_CONTRACT = 'declared-entry-spread-risk-quote-source@2'

def load_entry_spread_risk_plan_v2(market, parent, policy, *, client, batch_size=512):
    """Read only exact completed proposal quotes; no journal or future carry."""
    if (type(market) is not CertifiedMarketDayPlan or type(parent) is not EpisodeActivityStaticGate
            or type(policy) is not EntrySpreadRiskPolicy or type(batch_size) is not int
            or not 1 <= batch_size <= 1024):
        raise ValueError('Entry cost loader needs exact declared full-prefix inputs')
    verify_market_day_plan(market, client=client)
    arrays = tuple(np.zeros(len(parent.facts), dtype=np.int64) for _ in range(5))
    attempts = [''] * len(parent.facts)
    units = {(u.session_date, u.ticker): u for u in market.units if u.stage == 'broker_100ms'}
    selected = np.flatnonzero(parent.rejection_mask == 0).tolist()
    from .backtest_market_data import SESSION_OPEN_OFFSET_MS
    for start in range(0, len(selected), batch_size):
        indices = selected[start:start + batch_size]
        keys = {}
        for index in indices:
            fact = parent.facts[index]
            unit = units.get((market.sessions[0], fact.ticker))
            if unit is None:
                raise ValueError('Entry cost source omits a pinned liquidity attempt')
            key = (fact.ticker, (fact.boundary_ms + SESSION_OPEN_OFFSET_MS) // 100 - 1, unit.attempt_id)
            if key in keys:
                raise ValueError('Entry cost source keys duplicate')
            keys[key] = index
        scope = ','.join(f'({_literal(ticker)},{bucket},toUUID({_literal(attempt)}))' for ticker, bucket, attempt in keys)
        sql = ("SELECT l.ticker AS ticker,l.bucket_index AS bucket_index,"
               "toString(l.attempt_id) AS liquidity_attempt_id,l.bid_int AS bid_int,l.ask_int AS ask_int,"
               "l.quote_timestamp_us AS quote_timestamp_us,l.quote_valid AS quote_valid "
               "FROM arte.liquidity_100ms_v1 AS l "
               f"WHERE l.build_id={_literal(market.build_id)} AND l.session_date=toDate({_literal(market.sessions[0])}) "
               f"AND (l.ticker,l.bucket_index,l.attempt_id) IN ({scope}) "
               "SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow")
        rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
        seen = set()
        for row in rows:
            key = (row['ticker'], row['bucket_index'], row['liquidity_attempt_id'])
            if (key not in keys or key in seen
                    or type(row['quote_valid']) is not int or row['quote_valid'] not in (0, 1)
                    or any(type(row[v]) is not int for v in ('bid_int', 'ask_int', 'quote_timestamp_us'))):
                raise ValueError('Entry cost source has foreign, duplicate or unavailable quote')
            seen.add(key)
            index = keys[key]
            fact = parent.facts[index]
            values = (row['bid_int'], row['ask_int'], canonical_price_int(fact.stop_price), row['quote_timestamp_us'], row['quote_valid'])
            for column, value in zip(arrays, values):
                column[index] = value
            attempts[index] = row['liquidity_attempt_id']
        if seen != set(keys):
            raise ValueError(f'Entry cost source is missing exact proposal coverage: expected={len(keys)} observed={len(seen)}')
    return CertifiedEntrySpreadRiskPlan(market, parent, policy, *arrays, tuple(attempts),
        _token(market, parent, policy, arrays, tuple(attempts)))


@dataclass(frozen=True, slots=True)
class EntrySpreadRiskV2ReadbackAuthority(EntrySpreadRiskReadbackAuthority):
    def __post_init__(self):
        EntrySpreadRiskReadbackAuthority.__post_init__(self)
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
        if numbered_fixed_strategy(self.strategy_number).entry_spread_risk_quote_source_contract != QUOTE_SOURCE_CONTRACT:
            raise ValueError('Quote-source@2 authority requires exact declared source lineage')

"""Present one sealed V7 ticker without auditing every ticker's clock children.

Retained producer coverage discovers the cohort only. Its reconstructed complete
structure digest must match the saved run before the shared certificate reader
validates the requested ticker's actual clocks, intervals and causal geometry.
This grants no execution or producer-source certification authority.
"""
from hashlib import sha256
import json
import re
from uuid import UUID

from src.backend.backtest_market_data import _literal, project_market_day_plan
from src.backend.backtest_market_plan_cache import selected_product_inventory_fingerprint
from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
from src.backend.structural_v7_seed import certified_seed_plan
from src.trading_runtime.strategy_one_v7_interval_schema import COVERAGE_TABLE, PRODUCT_DIGEST
from src.trading_runtime.strategy_forty_three_fact_schema import COVERAGE_TABLE as SOURCE_COVERAGE


def recorded_structure_digest(market_token, seed_token, session, rows):
    token = sha256(b'strategy-one-v7-interval-plan-v1\0')
    for value in (market_token, seed_token, session, PRODUCT_DIGEST):
        token.update(value.encode()); token.update(b'\0')
    tickers = [row['ticker'] for row in rows]
    if not tickers or tickers != sorted(set(tickers)):
        raise RuntimeError('Saved V7 coverage cohort is missing or duplicated')
    for row in rows:
        if any(not re.fullmatch('[0-9a-f]{64}', row[name]) for name in ('clock_hash', 'interval_hash')):
            raise RuntimeError('Saved V7 coverage hash is invalid')
        for value in (row['ticker'], str(UUID(row['attempt_id'])), row['clock_hash'], row['interval_hash']):
            token.update(value.encode()); token.update(b'\0')
    return token.hexdigest()


def recorded_v7_ticker_intervals(client, *, market, session, ticker, structure_pin):
    where = (f'source_build_id={_literal(market.build_id)} '
             f'AND session_date=toDate({_literal(session)}) ')
    discovered = [json.loads(line)['ticker'] for line in client.execute(
        f'SELECT ticker FROM {SOURCE_COVERAGE} WHERE {where}'
        f'AND source_market_token={_literal(market.token)} AND source_v7_token={_literal(structure_pin)} '
        'ORDER BY ticker LIMIT 5001 FORMAT JSONEachRow').splitlines() if line.strip()]
    if (not 1 <= len(discovered) <= 5000 or discovered != sorted(set(discovered))
            or not set(discovered) <= set(market.tickers)):
        raise RuntimeError('Saved V7 producer cohort is missing or ambiguous')
    selected = tuple(discovered)
    tables = (COVERAGE_TABLE.split('.', 1)[1],)
    scope = dict(source_build_id=market.build_id, session_date=session, tickers=selected)
    before = selected_product_inventory_fingerprint(client, tables, **scope)
    seeds = certified_seed_plan(project_market_day_plan(market, selected), client)
    names = ','.join(_literal(symbol) for symbol in selected)
    rows = [json.loads(line) for line in client.execute(
        f'SELECT ticker,toString(derivation_attempt_id) AS attempt_id,clock_hash,interval_hash '
        f'FROM {COVERAGE_TABLE} WHERE {where}AND ticker IN ({names}) '
        f'ORDER BY ticker LIMIT {len(selected)+1} FORMAT JSONEachRow').splitlines() if line.strip()]
    if ([row['ticker'] for row in rows] != list(selected)
            or recorded_structure_digest(market.token, seeds.token, session, rows) != structure_pin):
        raise RuntimeError('Saved V7 structure certificate differs from the run')
    if ticker not in selected:
        return ()
    certified = certify_v7_interval_plan(market, seeds, session_date=session,
        candidate_tickers=(ticker,), client=client)
    if selected_product_inventory_fingerprint(client, tables, **scope) != before:
        raise RuntimeError('Saved V7 coverage changed during chart read')
    return dict(certified.intervals)[ticker]

"""Publish a proof-backed initial V7 state for a ticker with no canonical prefix.

This is initial state, not a synthetic completed source-day checkpoint. It uses
the existing empty-state representation and explicit disjoint campaign lineage.
"""
from datetime import date, datetime, timedelta, timezone
import re

from src.market_engine.historical_level_checkpoint import digest
from src.backend.structural_v7_seed import _cutoff
from src.backend.backtest_reference_identity import certify_reference_identity
from src.trading_runtime.historical_reference_identity import rows
from src.trading_runtime.structural_v7_lineage import TABLE, verify_table
from pipelines.market_sip.events.market_day_sql import literal

VERSION = 'canonical-no-prefix-initial-v7-1'
SOURCE_FILTER = 'flatfile_direct_events_v1|drop_trade_correction_codes=07,08,10,11|condition_slots=5'
COVERAGE = 'arte.structural_level_coverage_v7'


def certify_absence(*, session, ticker, certificates, expected_days,
                    metadata_days, prior_events):
    """Absence is valid only inside a complete, unfiltered canonical authority."""
    session = date.fromisoformat(session)
    if (not re.fullmatch(r'[A-Z0-9.-]{1,30}', ticker)
            or not certificates or not expected_days
            or metadata_days != 0 or prior_events != 0):
        raise ValueError('Initial V7 seed requires proven zero prior canonical history')
    days = [str(row['source_date']) for row in certificates]
    if (days != sorted(set(days)) or set(days) != set(expected_days)
            or any(day >= session.isoformat() for day in days)
            or any(row['source_filter_key'] != SOURCE_FILTER for row in certificates)):
        raise ValueError('Initial V7 seed requires complete unfiltered source-day certificates')
    return dict(version=VERSION, ticker=ticker, session=session.isoformat(),
                prior_metadata_days=0, prior_event_count=0,
                canonical_start=days[0], canonical_end=days[-1],
                source_days=len(days), certificates_hash=digest(certificates))


def source_proof(client, *, session, ticker):
    import pandas_market_calendars as mcal
    certificates = rows(client,
        'SELECT * FROM market_sip_compact.events_source_day_stats FINAL '
        f'WHERE source_date<toDate({literal(session)}) ORDER BY source_date')
    if not certificates:
        raise ValueError('Canonical source certificates unavailable')
    start = str(certificates[0]['source_date'])
    end = (date.fromisoformat(session) - timedelta(days=1)).isoformat()
    expected = [str(day.date()) for day in mcal.get_calendar('XNYS').schedule(
        start_date=start, end_date=end).index]
    metadata = rows(client,
        'SELECT count() n FROM market_sip_compact.events_ordinal_continuity FINAL '
        f'WHERE ticker={literal(ticker)} AND source_date<toDate({literal(session)})')
    # Independently scan the exclusive canonical tables; metadata absence alone
    # is insufficient. Bound each scan to one year and one ticker.
    total = 0
    cutoff_us = int(datetime.fromisoformat(
        _cutoff(date.fromisoformat(session))).replace(tzinfo=timezone.utc).timestamp() * 1_000_000)
    for year in range(date.fromisoformat(start).year, date.fromisoformat(session).year + 1):
        result = rows(client, f'SELECT count() n FROM market_sip_compact.events_{year} '
            f'WHERE ticker={literal(ticker)} AND event_date<=toDate({literal(session)}) '
            f'AND sip_timestamp_us<{cutoff_us}')
        total += int(result[0]['n'])
    return certify_absence(session=session, ticker=ticker,
        certificates=certificates, expected_days=expected,
        metadata_days=int(metadata[0]['n']), prior_events=total)


def publish_initial_seed(client, market, *, ticker, parent_hash, retain_proof):
    if len(market.sessions) != 1 or ticker not in market.tickers:
        raise ValueError('Initial seed needs one certified market session')
    session = market.sessions[0]
    identity = certify_reference_identity(market, client=client)
    proof = source_proof(client, session=session, ticker=ticker)
    proof.update(build_id=market.build_id, market_token=market.token,
                 identity_token=identity.token, parent_source_plan_hash=parent_hash)
    supplement = digest(proof)
    retain_proof(dict(proof, source_plan_hash=supplement))
    # Reuse exact existing V7 storage/schema checks; no fallback or new table.
    from scripts.migrate_level_book_v7_to_clickhouse import preflight, insert
    preflight(client)
    verify_table(client)
    if not re.fullmatch('[0-9a-f]{64}', parent_hash):
        raise ValueError('Invalid initial seed parent')
    if not rows(client, f'SELECT ticker FROM {COVERAGE} FINAL '
                f'WHERE source_plan_hash={literal(parent_hash)} LIMIT 1'):
        raise ValueError('Certified parent V7 coverage is absent')
    existing = rows(client, f'SELECT * FROM {COVERAGE} FINAL WHERE ticker={literal(ticker)}')
    prior_day = (date.fromisoformat(session) - timedelta(days=1)).isoformat()
    item = dict(ticker=ticker, session_date=prior_day,
        available_at=_cutoff(date.fromisoformat(session)), state='empty',
        level_count=0, interval_count=0, observation_count=0,
        observation_interval_count=0, input_policy='', source_extraction_version='',
        band_config_hash='0' * 64, source_input_hash=supplement,
        source_checkpoint_hash='', parent_checkpoint_hash='', source_plan_hash=supplement)
    if existing and (len(existing) != 1 or any(existing[0].get(k) != v for k, v in item.items())):
        raise ValueError('Initial V7 coverage conflicts with an existing publication')
    lineage = rows(client, f'SELECT parent_source_plan_hash,supplement_source_plan_hash,ticker '
                   f'FROM {TABLE} WHERE ticker={literal(ticker)}')
    expected_lineage = dict(parent_source_plan_hash=parent_hash,
                            supplement_source_plan_hash=supplement, ticker=ticker)
    if lineage and lineage != [expected_lineage]:
        raise ValueError('Initial V7 lineage conflicts with an existing publication')
    # Re-read all source proof before publishing. Never retry uncertain inserts.
    if source_proof(client, session=session, ticker=ticker) != {
            k: v for k, v in proof.items() if k not in (
                'build_id', 'market_token', 'identity_token', 'parent_source_plan_hash')}:
        raise ValueError('Canonical absence changed before initial-seed publication')
    stamp = datetime.now(timezone.utc)
    if not lineage:
        insert(client, TABLE, [dict(expected_lineage, verified_at=stamp.strftime('%Y-%m-%d %H:%M:%S.%f'))],
               supplement + '-lineage', batch_rows=1, batch_bytes=65536)
    if not existing:
        item.update(publication_revision=int(stamp.timestamp() * 1_000_000_000),
                    published_at=stamp.strftime('%Y-%m-%d %H:%M:%S.%f') + '000')
        insert(client, COVERAGE, [item], supplement + '-initial-state', batch_rows=1, batch_bytes=65536)
    from src.backend.structural_v7_seed import load_seed
    seed = load_seed(client, ticker=ticker, session=date.fromisoformat(session))
    if seed['levels'] or seed['source_checkpoint_hash']:
        raise ValueError('Initial V7 seed readback is not empty')
    verify_table(client)
    return supplement

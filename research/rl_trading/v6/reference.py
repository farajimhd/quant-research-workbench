"""Point-in-time reference fields with explicit missing-V7 provenance."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json

from research.rl_trading.v1 import reference_features
from research.rl_trading.v1.arte_sql import literal, query


def read_reference(client, day: date, listing: dict):
    """Return prior V7 seed when certified, otherwise masked level slots.

    A missing prior V7 coverage row is a known availability state, not grounds
    to drop a listing. A malformed or uncertified existing row still fails.
    Security float/split fields remain point-in-time from q_live in both paths.
    """
    try:
        return reference_features.read_reference(client, day, listing)
    except ValueError as error:
        if 'No certified prior-session structural V7 coverage' not in str(error):
            raise
    ticker = str(listing['ticker'])
    cutoff = ("toDateTime64(" + literal(reference_features.opening(day)) +
              ",9,'UTC')")
    coverage = query(client,
        'SELECT state,session_date FROM arte.structural_level_coverage_v7 FINAL '
        f'WHERE ticker={literal(ticker)} AND available_at<={cutoff} '
        'ORDER BY available_at DESC LIMIT 1')
    if coverage:
        raise ValueError('Existing V7 coverage is invalid, not missing')
    identity = reference_features._identity(listing)
    floats = query(client,
        'SELECT effective_date,free_float,shares_outstanding,inserted_at,'
        'source_content_sha256 FROM q_live.market_security_float_v1 FINAL '
        f'WHERE {identity} AND effective_date<=toDate({literal(day)}) '
        f'AND inserted_at<={cutoff} '
        'ORDER BY effective_date DESC,inserted_at DESC LIMIT 1')
    splits = query(client,
        'SELECT execution_date,split_from,split_to,inserted_at,'
        'source_content_sha256 FROM q_live.market_stock_split_v1 FINAL '
        f'WHERE {identity} AND execution_date<=toDate({literal(day)}) '
        f'AND inserted_at<={cutoff} '
        'ORDER BY execution_date DESC,inserted_at DESC')
    fundamental = reference_features.fundamentals(day, floats, splits)
    evidence = {'contract': 'v6-missing-prior-v7-masked',
                'ticker': ticker, 'day': str(day),
                'float': floats[0] if floats else None, 'splits': splits,
                'v7_coverage': 'absent'}
    evidence['hash'] = sha256(json.dumps(evidence, sort_keys=True,
                                         default=str).encode()).hexdigest()
    return None, [], fundamental, evidence

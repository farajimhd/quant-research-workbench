"""Coverage-last migration from retained broker identities to a pinned snapshot."""
from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from pipelines.market_sip.events.market_day_sql import literal
from src.backend.backtest_strategy_one_identity import identity_content_hash
from src.trading_runtime.historical_reference_identity import (
    IDENTITIES, COVERAGE, ReferenceIdentityError, rows, utc,
)

FIELDS = ('ticker', 'symbol_id', 'listing_id', 'security_id', 'ibkr_conid',
          'source_run_id', 'source_inserted_at')


def match_retained_identities(snapshot, retained, market, pin, *, resolved_listings=None):
    """Match the exact immutable identity tuple, source run, and capture clock.

    snapshot includes the entire pinned population, including non-tradable rows;
    row_hash is computed by ClickHouse using the original certificate expression.
    Broker conids were not included in that old hash. They are explicitly sealed
    as retained-source migration evidence now, never represented as historically
    hashed broker IDs.
    """
    if not snapshot or str(sum(int(r['row_hash']) for r in snapshot) % (1 << 64)) != pin.population_source_hash:
        raise ReferenceIdentityError('Snapshot contents differ from the market population hash')
    source_dates = {r['source_universe_date'] for r in snapshot}
    if len(source_dates) != 1:
        raise ReferenceIdentityError('Snapshot has mixed source dates')
    source_date = source_dates.pop()
    if source_date > market.sessions[0]:
        raise ReferenceIdentityError('Reference source date is after the target session')
    if pin.reference_revision == 'preopen-tradable-carry-forward-v1' and source_date >= market.sessions[0]:
        raise ReferenceIdentityError('Carried reference must come from an earlier date')
    selected = set(market.tickers)
    resolved_listings = resolved_listings or {}
    snapshots = {}
    for row in snapshot:
        if utc(row['captured_at_utc']) > utc(pin.available_at):
            raise ReferenceIdentityError('Reference capture is later than its certified availability')
        if row['ticker'] in selected and row['is_tradable'] == 1:
            resolved = resolved_listings.get(row['ticker'])
            if resolved and (any(row[k] != resolved[k] for k in
                    ('symbol_id', 'listing_id', 'security_id', 'source_run_id'))
                    or utc(row['captured_at_utc']) != utc(resolved['source_inserted_at'])):
                continue  # V3 proof explicitly selects a tuple; original hash covers every row.
            if row['ticker'] in snapshots:
                raise ReferenceIdentityError('Snapshot has ambiguous selected listing identity')
            snapshots[row['ticker']] = row
    if set(snapshots) != selected:
        raise ReferenceIdentityError('Snapshot does not contain the complete market population')
    output = {}
    for row in retained:
        ticker = row['ticker']
        if ticker not in selected:
            continue
        resolved = resolved_listings.get(ticker)
        if resolved and any(row.get(k) != resolved.get(k) for k in FIELDS):
            continue  # Preserve secondary source rows; never silently deduplicate V1/V2.
        expected = snapshots[ticker]
        if ticker in output or any(row.get(k) != expected[k] for k in
                ('symbol_id', 'listing_id', 'security_id', 'source_run_id')):
            raise ReferenceIdentityError(f'Retained identity differs from snapshot: {ticker}')
        if utc(row['source_inserted_at']) != utc(expected['captured_at_utc']):
            raise ReferenceIdentityError(f'Retained capture clock differs from snapshot: {ticker}')
        conid = row.get('ibkr_conid')
        if not isinstance(conid, str) or not conid.isdecimal() or int(conid) <= 0:
            raise ReferenceIdentityError(f'Retained broker ID is unavailable: {ticker}')
        if any(not isinstance(row.get(k), str) or not row[k] for k in
               ('symbol_id', 'listing_id', 'security_id', 'source_run_id')):
            raise ReferenceIdentityError(f'Retained identity is incomplete: {ticker}')
        output[ticker] = {k: row[k] for k in FIELDS}
        output[ticker]['ibkr_conid'] = int(conid)
    if set(output) != selected:
        raise ReferenceIdentityError(f'Retained identity missing {len(selected - output.keys())} tickers')
    return source_date, [output[t] for t in market.tickers]


def resolve_retained_identities(client, market, pin):
    snapshot = rows(client,
        'SELECT ticker,symbol_id,listing_id,security_id,is_tradable,source_run_id,'
        'source_universe_date,captured_at_utc,'
        'cityHash64(tuple(ticker,symbol_id,listing_id,security_id,is_tradable,'
        'exclusion_reason,source_run_id,captured_at_utc)) AS row_hash '
        'FROM q_live.feature_tradable_universe_snapshot_v2 '
        f'WHERE session_date=toDate({literal(market.sessions[0])}) '
        f'AND snapshot_id={literal(pin.snapshot_id)}')
    dates = {r['source_universe_date'] for r in snapshot}
    if len(dates) != 1:
        raise ReferenceIdentityError('Pinned reference snapshot is missing or has mixed dates')
    source_date = dates.pop()
    retained = rows(client, 'SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,'
        'source_run_id,inserted_at AS source_inserted_at '
        'FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(source_date)}) AND is_tradable=1')
    return match_retained_identities(snapshot, retained, market, pin)


def publish_reference_identity(client, reader, market, pin):
    from src.backend.backtest_reference_identity import certify_reference_identity
    try:
        source_date, expected = resolve_retained_identities(client, market, pin)
    except ReferenceIdentityError as exc:
        if str(exc) != 'Snapshot has ambiguous selected listing identity':
            raise
        from pipelines.strategy_one.reference_identity_v3_publication import publish_reference_identity_v3
        return publish_reference_identity_v3(client, reader, market, pin)
    print(f'Reference: session={market.sessions[0]} source={source_date} '
          f'revision={pin.reference_revision} tickers={len(expected)}', flush=True)
    digest = identity_content_hash(expected)
    predicate = (f'source_build_id={literal(market.build_id)} '
                 f'AND session_date=toDate({literal(market.sessions[0])})')
    legacy = rows(reader, 'SELECT identity_attempt_id FROM arte.strategy_one_identity_coverage_v1 '
                  f'WHERE {predicate}')
    if legacy:
        from src.backend.backtest_strategy_one_identity import certify_identity_plan
        old = certify_identity_plan(market, client=reader)
        if old.content_hash != digest:
            raise ReferenceIdentityError('Existing V1 identities differ from pinned reference; migration withheld')
        return 'skipped_existing_v1'
    existing = rows(reader, f'SELECT identity_attempt_id FROM arte.{COVERAGE.name} WHERE {predicate}')
    if existing:
        sealed = certify_reference_identity(market, client=reader, pin=pin)
        if sealed.content_hash != digest:
            raise ReferenceIdentityError('Retained broker identity changed after publication')
        return 'skipped'
    attempt = str(uuid4())
    for start in range(0, len(expected), 500):
        values = []
        for row in expected[start:start + 500]:
            values.append('(' + ','.join((literal(market.build_id),
                literal(market.sessions[0]), literal(attempt),
                *(literal(row[k]) if k != 'ibkr_conid' else str(row[k]) for k in FIELDS))) + ')')
        client.execute(f'INSERT INTO arte.{IDENTITIES.name} '
            '(source_build_id,session_date,identity_attempt_id,' + ','.join(FIELDS) +
            ') VALUES ' + ','.join(values))
    observed = rows(reader, f"SELECT {','.join(FIELDS)} FROM arte.{IDENTITIES.name} "
        f'WHERE {predicate} AND identity_attempt_id={literal(attempt)} ORDER BY ticker')
    if observed != expected or identity_content_hash(observed) != digest:
        raise ReferenceIdentityError('Identity child readback differs; coverage withheld')
    # A second source read prevents publishing a mixed-time migration. An
    # interruption before coverage leaves invisible children under this UUID.
    if resolve_retained_identities(client, market, pin) != (source_date, expected):
        raise ReferenceIdentityError('Retained reference changed during publication')
    values = [market.build_id, market.sessions[0], attempt, source_date,
              *asdict(pin).values(), len(expected), digest, pin.digest(source_date)]
    names = [name for name, _ in COVERAGE.columns[:-1]]
    client.execute(f"INSERT INTO arte.{COVERAGE.name} ({','.join(names)},certified_at) VALUES (" +
        ','.join(str(v) if type(v) is int else literal(v) for v in values) + ",now64(6,'UTC'))")
    if certify_reference_identity(market, client=reader, pin=pin).attempt_id != attempt:
        raise ReferenceIdentityError('Identity coverage did not certify')
    return 'published'

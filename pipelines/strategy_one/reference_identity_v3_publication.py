"""Coverage-last publication of source-bound dated listing resolutions."""
from dataclasses import asdict
from uuid import uuid4

from pipelines.market_sip.events.market_day_sql import literal
from pipelines.strategy_one.reference_identity_publication import FIELDS
from src.backend.backtest_strategy_one_identity import identity_content_hash
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.historical_reference_identity import rows, ReferenceIdentityError
from src.trading_runtime.historical_reference_identity_v3 import (
    IDENTITIES, PROOFS, COVERAGE, TABLES, PROOF_FIELDS,
    resolution_plan, normalized_proofs, proof_hash,
)


def load_resolution(client, market, pin):
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
        raise ReferenceIdentityError('V3 reference snapshot has missing or mixed source dates')
    source_date = dates.pop()
    retained = rows(client,
        'SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,source_run_id,'
        'inserted_at AS source_inserted_at FROM q_live.feature_tradable_universe_v1 FINAL '
        f'WHERE universe_date=toDate({literal(source_date)}) AND is_tradable=1')
    counts = {}
    for row in snapshot:
        if row['is_tradable'] == 1 and row['ticker'] in market.tickers:
            counts[row['ticker']] = counts.get(row['ticker'], 0) + 1
    conflicts = sorted(t for t, n in counts.items() if n > 1)
    if not conflicts:
        raise ReferenceIdentityError('V3 publisher requires conflicting sealed listings')
    # Read raw historical versions, never FINAL/current graph bindings. The pin
    # bounds both evidence capture and resolution, including carried sessions.
    mappings = rows(client,
        'SELECT source_system,source_entity_key,mapped_entity_kind,mapped_entity_id,'
        'source_content_sha256,evidence_json,resolved_at_utc,inserted_at '
        'FROM q_live.id_source_mapping_v1 WHERE source_system=\'massive\' '
        f"AND source_entity_key IN ({','.join(literal(t) for t in conflicts)}) "
        f"AND inserted_at<=toDateTime64({literal(pin.available_at)},6,'UTC') "
        f"AND resolved_at_utc<=toDateTime64({literal(pin.available_at)},6,'UTC')")
    day, identities, payload, _ = resolution_plan(snapshot, retained, mappings, market, pin)
    proofs = normalized_proofs(payload)
    return day, identities, proofs


def _insert(client, table, columns, records):
    for start in range(0, len(records), 500):
        values = ['(' + ','.join(str(v) if type(v) is int else literal(v) for v in row) + ')'
                  for row in records[start:start + 500]]
        client.execute(f"INSERT INTO arte.{table.name} ({','.join(columns)}) VALUES " + ','.join(values))


def publish_reference_identity_v3(client, reader, market, pin):
    from src.backend.backtest_reference_identity import certify_reference_identity
    storage_preflight(reader, tables=TABLES)
    source_date, expected, proofs = load_resolution(client, market, pin)
    digest, resolution_digest = identity_content_hash(expected), proof_hash(proofs)
    predicate = f'source_build_id={literal(market.build_id)} AND session_date=toDate({literal(market.sessions[0])})'
    existing = rows(reader, f'SELECT identity_attempt_id FROM arte.{COVERAGE.name} WHERE {predicate}')
    if existing:
        certified = certify_reference_identity(market, client=reader, pin=pin, _v3=True)
        seals = rows(reader, f'SELECT resolution_hash FROM arte.{COVERAGE.name} WHERE {predicate}')
        if certified.content_hash != digest or seals != [{'resolution_hash': resolution_digest}]:
            raise ReferenceIdentityError('Existing V3 identity differs from retained evidence')
        return 'skipped_v3'
    attempt = str(uuid4())
    prefix = [market.build_id, market.sessions[0], attempt]
    _insert(client, IDENTITIES, [name for name, _ in IDENTITIES.columns],
            [prefix + [r[k] for k in FIELDS] for r in expected])
    _insert(client, PROOFS, [name for name, _ in PROOFS.columns],
            [prefix + [r[k] for k in PROOF_FIELDS] for r in proofs])
    child_predicate = predicate + f' AND identity_attempt_id={literal(attempt)}'
    observed = rows(reader, f"SELECT {','.join(FIELDS)} FROM arte.{IDENTITIES.name} WHERE {child_predicate} ORDER BY ticker")
    observed_proofs = rows(reader, f"SELECT {','.join(PROOF_FIELDS)} FROM arte.{PROOFS.name} WHERE {child_predicate} ORDER BY ticker,mapping_content_hash")
    if observed != expected or observed_proofs != proofs:
        raise ReferenceIdentityError('V3 child readback differs; coverage withheld')
    if load_resolution(client, market, pin) != (source_date, expected, proofs):
        raise ReferenceIdentityError('V3 source changed during publication; coverage withheld')
    values = [*prefix, source_date, *asdict(pin).values(), len(expected), digest,
              pin.digest(source_date), len(proofs), resolution_digest]
    columns = [name for name, _ in COVERAGE.columns[:-1]]
    client.execute(f"INSERT INTO arte.{COVERAGE.name} ({','.join(columns)},certified_at) VALUES (" +
        ','.join(str(v) if type(v) is int else literal(v) for v in values) + ",now64(6,'UTC'))")
    if certify_reference_identity(market, client=reader, pin=pin, _v3=True).attempt_id != attempt:
        raise ReferenceIdentityError('V3 coverage did not certify')
    return 'published_v3'

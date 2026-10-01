"""Immutable dated-listing resolution contracts, separate from V1/V2 identity."""
from dataclasses import asdict
from hashlib import sha256
import json

from src.trading_runtime.arte_journal_schema import TableContract, storage_preflight
from src.trading_runtime.historical_reference_identity import (
    COVERAGE as V2_COVERAGE, IDENTITIES as V2_IDENTITIES,
    ReferenceIdentityError, rows,
)

IDENTITIES = TableContract("strategy_one_identity_v3", V2_IDENTITIES.columns,
    V2_IDENTITIES.partition, V2_IDENTITIES.order)
PROOFS = TableContract("strategy_one_identity_resolution_v3", (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("identity_attempt_id", "UUID"), ("ticker", "String"),
    ("selected_symbol_id", "String"), ("selected_listing_id", "String"),
    ("selected_security_id", "String"), ("selected_ibkr_conid", "UInt64"),
    ("mapping_content_hash", "FixedString(64)"),
    ("mapping_resolved_at", "DateTime64(6, 'UTC')"),
    ("mapping_inserted_at", "DateTime64(6, 'UTC')"),
    ("resolution_revision", "String")), "toYYYYMM(session_date)",
    "source_build_id,session_date,identity_attempt_id,ticker,mapping_content_hash")
COVERAGE = TableContract("strategy_one_identity_coverage_v3",
    (*V2_COVERAGE.columns[:-1], ("resolution_hash", "FixedString(64)"),
     V2_COVERAGE.columns[-1]), V2_COVERAGE.partition, V2_COVERAGE.order)
TABLES = (IDENTITIES, PROOFS, COVERAGE)
REVISION = "reference-identity-v3"


def resolution_plan(snapshot, retained, mappings, market, pin):
    """Preserve the full population hash and prove every selected conflict."""
    from pipelines.strategy_one.dated_listing_resolution import resolve_listing
    from pipelines.strategy_one.reference_identity_publication import match_retained_identities
    counts = {}
    for row in snapshot:
        if row['is_tradable'] == 1 and row['ticker'] in market.tickers:
            counts[row['ticker']] = counts.get(row['ticker'], 0) + 1
    conflicting = sorted(t for t, n in counts.items() if n > 1)
    if not conflicting:
        raise ReferenceIdentityError("V3 resolution requires a conflicting sealed population")
    resolutions = {}
    for ticker in conflicting:
        chosen, _, _ = resolve_listing(snapshot, retained, mappings, ticker=ticker, pin=pin)
        resolutions[ticker] = chosen
    source_date, expected = match_retained_identities(
        snapshot, retained, market, pin, resolved_listings=resolutions)
    used_mappings = [r for r in mappings if r['source_entity_key'] in conflicting
                     and r['source_system'] == 'massive']
    # Sorted raw evidence is replayable and includes both selected and rejected
    # listing tuples. Hashes never depend on ClickHouse insertion order.
    stable = lambda values: sorted(values, key=lambda r: json.dumps(r, sort_keys=True))
    proof = dict(revision=REVISION, market_tickers=list(market.tickers),
                 pin=asdict(pin), snapshot=stable(snapshot), retained=stable(retained),
                 mappings=stable(used_mappings))
    payload = json.dumps(proof, sort_keys=True, separators=(',', ':'))
    return source_date, expected, payload, sha256(payload.encode()).hexdigest()


def verify_resolution(payload, digest, market, pin):
    if sha256(payload.encode()).hexdigest() != digest:
        raise ReferenceIdentityError("Listing resolution payload hash differs")
    proof = json.loads(payload)
    if proof['revision'] != REVISION or proof['pin'] != asdict(pin):
        raise ReferenceIdentityError("Listing resolution provenance differs")
    from dataclasses import replace
    full_market = replace(market, tickers=tuple(proof['market_tickers']))
    if not set(market.tickers) <= set(full_market.tickers):
        raise ReferenceIdentityError("Listing resolution omits the requested market population")
    result = resolution_plan(proof['snapshot'], proof['retained'], proof['mappings'], full_market, pin)
    if result[2:] != (payload, digest):
        raise ReferenceIdentityError("Listing resolution is not canonical")
    return result[:2]


def install_tables(client):
    if rows(client, "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'") != [{'disks': ['live_market_ssd']}]:
        raise ReferenceIdentityError("V3 historical identities require SSD-only storage")
    for table in TABLES:
        client.execute(table.ddl())
    storage_preflight(client, tables=TABLES)


"""Read-only certification of snapshot-linked broker identity publications."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import re

from src.trading_runtime.historical_reference_identity import (
    COVERAGE, IDENTITIES, ReferenceIdentityError, load_reference_pin, rows, utc, verify_tables,
)


def certify_reference_identity(market, *, client, pin=None, _v3=False):
    from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan, identity_content_hash
    # This path is used only when no legacy publication exists. Existing V1
    # attempts retain their exact token, including for saved-run recovery.
    coverage_table, identity_table = COVERAGE, IDENTITIES
    if _v3:
        from src.trading_runtime.historical_reference_identity_v3 import (
            COVERAGE as coverage_table, IDENTITIES as identity_table,
            PROOFS, TABLES, PROOF_FIELDS, certify_proofs,
        )
        from src.trading_runtime.arte_journal_schema import storage_preflight
    present = rows(client, "SELECT name FROM system.tables WHERE database='arte' "
                   f"AND name='{coverage_table.name}'")
    if present != [{'name': coverage_table.name}]:
        raise ReferenceIdentityError('Snapshot-linked identity publication is not installed')
    if _v3:
        storage_preflight(client, tables=TABLES)
    else:
        verify_tables(client)
    predicate = f"source_build_id='{market.build_id}' AND session_date='{market.sessions[0]}'"
    coverage = rows(client, f'SELECT * FROM arte.{coverage_table.name} WHERE {predicate}')
    if not coverage and not _v3:
        return certify_reference_identity(market, client=client, pin=pin, _v3=True)
    if len(coverage) != 1:
        raise ReferenceIdentityError('Missing or ambiguous snapshot-linked identity publication')
    seal = coverage[0]
    pin = pin or load_reference_pin(client, market)
    source_date = seal['source_universe_date']
    if (any(seal[k] != v for k, v in asdict(pin).items())
            or seal['reference_hash'] != pin.digest(source_date)
            or source_date > market.sessions[0]
            or (pin.reference_revision == 'preopen-tradable-carry-forward-v1'
                and source_date >= market.sessions[0])):
        raise ReferenceIdentityError('Identity provenance differs from the sealed market population')
    attempt = seal['identity_attempt_id']
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', attempt):
        raise ReferenceIdentityError('Identity attempt is invalid')
    facts = rows(client, 'SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,'
        f'source_run_id,source_inserted_at FROM arte.{identity_table.name} WHERE {predicate} '
        f"AND identity_attempt_id='{attempt}' ORDER BY ticker")
    tickers = tuple(r['ticker'] for r in facts)
    if (not facts or len(facts) != seal['ticker_count']
            or len(set(tickers)) != len(tickers) or tuple(sorted(tickers)) != tickers
            or not set(market.tickers) <= set(tickers)
            or any(type(r['ibkr_conid']) is not int or r['ibkr_conid'] <= 0 for r in facts)
            or any(not isinstance(r[k], str) or not r[k] for r in facts
                   for k in ('symbol_id', 'listing_id', 'security_id', 'source_run_id'))
            or any(utc(r['source_inserted_at']) > utc(pin.available_at) for r in facts)
            or identity_content_hash(facts) != seal['content_hash']):
        raise ReferenceIdentityError('Snapshot-linked identity rows differ from their publication seal')
    token_fields = ('reference-identity-v2', market.build_id,
        market.sessions[0], attempt, market.token, seal['content_hash'], seal['reference_hash'])
    if _v3:
        proofs = rows(client, f"SELECT {','.join(PROOF_FIELDS)} FROM arte.{PROOFS.name} "
            f"WHERE {predicate} AND identity_attempt_id='{attempt}' ORDER BY ticker,mapping_content_hash")
        certify_proofs(facts, proofs, seal, pin)
        token_fields = ('reference-identity-v3', *token_fields[1:], seal['resolution_hash'])
    token = sha256(json.dumps(token_fields,
        separators=(',', ':')).encode()).hexdigest()
    conids = {r['ticker']: r['ibkr_conid'] for r in facts}
    return CertifiedIdentityPlan(market.build_id, market.sessions[0], attempt,
        market.token, market.tickers, tuple(conids[t] for t in market.tickers),
        seal['content_hash'], token)

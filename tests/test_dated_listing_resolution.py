from copy import deepcopy
from hashlib import sha256
import json

import pytest

from pipelines.strategy_one.dated_listing_resolution import resolve_listing
from src.trading_runtime.historical_reference_identity import ReferenceIdentityError
from tests.test_historical_reference_identity import fixture
from src.trading_runtime.historical_reference_identity_v3 import resolution_plan, verify_resolution
from dataclasses import replace


def inputs():
    snapshot, retained, _, pin = fixture()
    alternate = {**snapshot[0], "symbol_id": "secondary-symbol", "listing_id": "secondary-listing"}
    snapshot.append(alternate)
    retained.append({**retained[0], "symbol_id": alternate["symbol_id"],
                     "listing_id": alternate["listing_id"], "ibkr_conid": "202"})
    data = {"ticker": "AAA", "name": "AAA Incorporated", "primary_exchange": "XNAS",
            "ibkr_candidates": [{"conid": 101, "ticker": "AAA", "name": "AAA Incorporated",
                                 "assetClass": "STK", "currency": "USD", "countryCode": "US",
                                 "isUS": True, "listingExchange": "NASDAQ"}]}
    raw = json.dumps(data)
    mappings = [{"source_system": "massive", "source_entity_key": "AAA",
                 "mapped_entity_kind": "market_symbol", "mapped_entity_id": retained[0]["symbol_id"],
                 "evidence_json": raw, "source_content_sha256": sha256(raw.encode()).hexdigest(),
                 "resolved_at_utc": "2026-08-19 05:00:00", "inserted_at": "2026-08-19 05:00:00"}]
    return snapshot, retained, mappings, pin


def test_resolution_binds_exact_identity_and_preserves_inputs():
    snapshot, retained, mappings, pin = inputs()
    original = deepcopy((snapshot, retained, mappings))
    chosen, payload, digest = resolve_listing(snapshot, retained, mappings, ticker="AAA", pin=pin)
    assert chosen == retained[0]
    assert sha256(payload.encode()).hexdigest() == digest
    assert json.loads(payload)["population_source_hash"] == pin.population_source_hash
    assert (snapshot, retained, mappings) == original
    assert resolve_listing(snapshot[::-1], retained[::-1], mappings, ticker="AAA", pin=pin)[2] == digest


@pytest.mark.parametrize("fault", ["late", "hash", "missing", "duplicate", "clock", "listing", "ticker", "conflict", "mapping"])
def test_rejects_unproven_identity_selection(fault):
    snapshot, retained, mappings, pin = inputs()
    if fault == "late": mappings[0]["inserted_at"] = "2026-08-20 05:00:00"
    if fault == "hash": mappings[0]["source_content_sha256"] = "0" * 64
    if fault == "missing": mappings.clear()
    if fault == "duplicate": retained.append(deepcopy(retained[0]))
    if fault == "clock": retained[0]["source_inserted_at"] = "2026-08-19 06:01:00"
    if fault == "listing": retained[0]["listing_id"] = "unsealed-listing"
    if fault == "mapping": mappings[0]["mapped_entity_id"] = "different-symbol"
    if fault in ("ticker", "conflict"):
        bad = deepcopy(mappings[0])
        data = json.loads(bad["evidence_json"])
        if fault == "ticker": data["ticker"] = "BBB"
        else: data["ibkr_candidates"][0]["conid"] = 202
        bad["evidence_json"] = json.dumps(data)
        bad["source_content_sha256"] = sha256(bad["evidence_json"].encode()).hexdigest()
        mappings.append(bad)
    with pytest.raises(ReferenceIdentityError):
        resolve_listing(snapshot, retained, mappings, ticker="AAA", pin=pin)


def test_versioned_proof_replays_full_population_and_retains_secondary_rows():
    snapshot, retained, mappings, pin = inputs()
    _, _, market, _ = fixture()
    snapshot[-1]['row_hash'] = 1
    pin = replace(pin, population_source_hash='3')
    source_date, expected, payload, digest = resolution_plan(snapshot, retained, mappings, market, pin)
    assert len(json.loads(payload)['snapshot']) == 3
    assert len(json.loads(payload)['retained']) == 3
    assert verify_resolution(payload, digest, market, pin) == (source_date, expected)
    assert [r['ticker'] for r in expected] == ['AAA', 'BBB']
    with pytest.raises(ReferenceIdentityError):
        verify_resolution(payload + ' ', digest, market, pin)
    with pytest.raises(ReferenceIdentityError):
        resolution_plan(snapshot, retained, mappings, market, replace(pin, population_source_hash='2'))

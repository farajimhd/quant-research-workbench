"""Versioned, offline resolution proof for conflicting sealed listing tuples.

This does not publish identities or change a population. Publishers must seal
the returned proof alongside the selected identity and revalidate it on read.
"""
from hashlib import sha256
import json

from services.reference_gateway.ibkr_contract_identity import resolve_massive_ibkr_contract
from src.trading_runtime.historical_reference_identity import ReferenceIdentityError, utc

REVISION = "dated-broker-listing-resolution-v1"
KEYS = ("ticker", "symbol_id", "listing_id", "security_id", "source_run_id")


def resolve_listing(snapshot, retained, mappings, *, ticker, pin):
    """Require one broker conid and one exact tuple from pre-pin evidence.

    Callers supply the entire certified snapshot and the retained source date,
    including for carried sessions. No latest graph or venue heuristic is used.
    """
    candidates = [r for r in snapshot if r["ticker"] == ticker and r["is_tradable"] == 1]
    if len(candidates) < 2:
        raise ReferenceIdentityError("Resolution requires conflicting snapshot listings")
    if any(utc(r["captured_at_utc"]) > utc(pin.available_at) for r in candidates):
        raise ReferenceIdentityError("Listing snapshot was unavailable at the reference pin")
    evidence = [r for r in mappings if r["source_system"] == "massive"
                and r["source_entity_key"] == ticker]
    if not evidence:
        raise ReferenceIdentityError("Dated broker listing evidence is missing")
    conids = set()
    for row in evidence:
        if any(utc(row[k]) > utc(pin.available_at) for k in ("inserted_at", "resolved_at_utc")):
            raise ReferenceIdentityError("Broker listing evidence is later than the reference pin")
        raw = row["evidence_json"]
        if sha256(raw.encode()).hexdigest() != row["source_content_sha256"]:
            raise ReferenceIdentityError("Broker listing evidence content hash differs")
        data = json.loads(raw)
        if data.get("ticker") != ticker:
            raise ReferenceIdentityError("Broker listing evidence belongs to another ticker")
        result = resolve_massive_ibkr_contract(
            massive_ticker=ticker, massive_name=data.get("name", ""),
            massive_exchange=data.get("primary_exchange", ""),
            definitions=data.get("ibkr_candidates", ()))
        if not result.accepted:
            raise ReferenceIdentityError("Dated broker listing evidence does not resolve uniquely")
        conids.add(str(result.conid))
    if len(conids) != 1:
        raise ReferenceIdentityError("Dated broker listing evidence conflicts")
    matches = [r for r in retained if r["ticker"] == ticker and r["ibkr_conid"] in conids]
    if len(matches) != 1:
        raise ReferenceIdentityError("Resolved broker identity is missing or duplicated in retained source")
    chosen = matches[0]
    if any(r.get("mapped_entity_kind") != "market_symbol"
           or r.get("mapped_entity_id") != chosen["symbol_id"] for r in evidence):
        raise ReferenceIdentityError("Broker evidence mapping differs from the selected symbol")
    exact = [r for r in candidates if all(r[k] == chosen[k] for k in KEYS)
             and utc(r["captured_at_utc"]) == utc(chosen["source_inserted_at"])]
    if len(exact) != 1:
        raise ReferenceIdentityError("Resolved identity does not match one sealed listing tuple")
    evidence = sorted(evidence, key=lambda r: json.dumps(r, sort_keys=True, separators=(",", ":")))
    proof = {"revision": REVISION, "snapshot_id": pin.snapshot_id,
             "population_source_hash": pin.population_source_hash,
             "available_at": pin.available_at, "ticker": ticker,
             "identity": chosen, "mapping_evidence": evidence}
    payload = json.dumps(proof, sort_keys=True, separators=(",", ":"))
    return chosen, payload, sha256(payload.encode()).hexdigest()

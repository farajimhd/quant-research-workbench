from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


CONTRACT_VERSION = "sec_synthesis_v1"
ENGINE_VERSION = "sec_synthesis_engine_v1_1"
REGISTRY_VERSION = "sec_synthesis_concepts_v1"
PRODUCTION_VERSION = "sec_synthesis_production_v1"
RENDERER_VERSION = "sec_synthesis_renderer_v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_document(document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "contract_version", "concept_registry_version", "accession_number", "cik",
        "accepted_at_utc", "source_hash", "filing_envelope", "entities",
        "narrative_disclosures", "fundamental_transitions", "reconciliation",
        "issuer_views", "synthesis", "eligibility", "quality_flags", "production",
    }
    missing = sorted(required - set(document))
    if missing:
        errors.append(f"missing fields: {missing}")
    if document.get("contract_version") != CONTRACT_VERSION:
        errors.append("invalid contract_version")
    if document.get("concept_registry_version") != REGISTRY_VERSION:
        errors.append("invalid concept_registry_version")
    if not str(document.get("accession_number") or ""):
        errors.append("accession_number is empty")
    if not str(document.get("cik") or ""):
        errors.append("cik is empty")
    disclosure_ids = [str(row.get("disclosure_id") or "") for row in document.get("narrative_disclosures", [])]
    transition_ids = [str(row.get("transition_id") or "") for row in document.get("fundamental_transitions", [])]
    if len(disclosure_ids) != len(set(disclosure_ids)) or "" in disclosure_ids:
        errors.append("narrative disclosure identities are invalid")
    if len(transition_ids) != len(set(transition_ids)) or "" in transition_ids:
        errors.append("fundamental transition identities are invalid")
    for row in document.get("narrative_disclosures", []):
        if not row.get("evidence"):
            errors.append(f"{row.get('disclosure_id')}: missing evidence")
    production = document.get("production") or {}
    if production.get("production_version") != PRODUCTION_VERSION:
        errors.append("invalid production_version")
    if production.get("engine_version") != ENGINE_VERSION:
        errors.append("invalid engine_version")
    return errors

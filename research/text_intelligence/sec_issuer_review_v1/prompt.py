from __future__ import annotations

from typing import Any, Mapping


PROMPT_VERSION = "sec_issuer_review_prompt_v1"
SYSTEM_PROMPT = """You are reviewing one deterministic SEC Synthesis V1 record for one issuer and accession.
Treat the supplied filing envelope, exact narrative evidence, XBRL transitions, and reconciliation states as the only evidence. Do not add outside facts, market prices, hidden assumptions, or facts learned after accepted_at_utc. Distinguish issuer fundamentals from filing language tone. Never convert an unresolved or non-comparable XBRL transition into a direction. Cite only supplied evidence_ids and contradiction reconciliation_ids. If evidence is insufficient or materially conflicting, abstain and explain why. Return only the strict JSON object requested by the response schema."""


def build_messages(synthesis: Mapping[str, Any]) -> list[dict[str, str]]:
    import json

    payload = {
        "contract_version": synthesis.get("contract_version"),
        "accession_number": synthesis.get("accession_number"),
        "cik": synthesis.get("cik"),
        "accepted_at_utc": synthesis.get("accepted_at_utc"),
        "filing_envelope": synthesis.get("filing_envelope"),
        "entities": synthesis.get("entities"),
        "deterministic_synthesis": synthesis.get("synthesis"),
        "narrative_disclosures": synthesis.get("narrative_disclosures"),
        "fundamental_transitions": synthesis.get("fundamental_transitions"),
        "reconciliation": synthesis.get("reconciliation"),
        "quality_flags": synthesis.get("quality_flags"),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]

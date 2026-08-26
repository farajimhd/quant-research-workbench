from __future__ import annotations

import json
from typing import Any, Callable

from research.text_intelligence.news_synthesis_v1.engine import ENGINE_VERSION
from research.text_intelligence.news_synthesis_v1.storage import (
    LIVE_SEMANTIC_TABLE,
    SYNTHESIS_TABLE,
)
from src.backend.query_plans.text_intelligence_consumer_v1 import (
    news_synthesis_by_id,
)


def load_news_synthesis(
    source_ids: list[str], *, query_rows: Callable[[str], list[dict[str, Any]]], quote: Callable[[str], str]
) -> dict[str, dict[str, Any]]:
    ids = sorted({value.strip() for value in source_ids if value.strip()})
    if not ids:
        return {}
    rows = query_rows(
        news_synthesis_by_id(
            ids,
            engine_version=ENGINE_VERSION,
            synthesis_table=SYNTHESIS_TABLE,
            quote=quote,
        )
    )
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        try: document = json.loads(str(row.get("synthesis_json") or "{}"))
        except json.JSONDecodeError: continue
        source_id = str(row.get("canonical_news_id") or "")
        output[source_id] = presentation_payload(document)
    return output


def presentation_payload(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "document": document,
        "summary": synthesis_summary(document),
        "article_fields": article_fields(document),
    }


def synthesis_summary(
    document: dict[str, Any], *, ticker: str = ""
) -> dict[str, Any] | None:
    """Return a compact V1-native presentation derived only from V1 fields."""
    entities = {
        str(row.get("entity_id") or ""): row for row in document.get("entities", [])
    }
    views = list(document.get("issuer_views", []))
    target = ticker.strip().upper()
    if target:
        views = [
            view
            for view in views
            if str(entities.get(str(view.get("entity_id") or ""), {}).get("ticker") or "").upper()
            == target
        ]
    if not views:
        return None
    eligible = {
        (str(row.get("entity_id") or ""), str(row.get("product") or "")): bool(
            row.get("eligible")
        )
        for row in document.get("eligibility", [])
    }
    strongest = max(
        views,
        key=lambda row: max(
            int(row.get("positive_strength") or 0),
            int(row.get("negative_strength") or 0),
        ),
    )
    entity_ids = [str(row.get("entity_id") or "") for row in views]
    return {
        "communication_purpose": document["envelope"]["communication_purpose"]["value"],
        "information_origin": document["envelope"]["information_origin"]["value"],
        "concepts": sorted(
            {str(row.get("concept_leaf") or "") for row in document.get("statements", []) if row.get("concept_leaf")}
        ),
        "composite_sentiment": strongest["composite_sentiment"],
        "positive_strength": int(strongest.get("positive_strength") or 0),
        "negative_strength": int(strongest.get("negative_strength") or 0),
        "forecast_trigger_eligible": any(
            eligible.get((entity_id, "forecast_trigger"), False) for entity_id in entity_ids
        ),
        "reaction_evaluation_eligible": any(
            eligible.get((entity_id, "reaction_study"), False) for entity_id in entity_ids
        ),
        "issuer_history_context_eligible": any(
            eligible.get((entity_id, "issuer_history"), False) for entity_id in entity_ids
        ),
        "analyst_evaluation_eligible": any(
            eligible.get((entity_id, "analyst_evaluation"), False) for entity_id in entity_ids
        ),
        "issuer_count": len(views),
        "engine_version": str(
            document.get("production", {}).get("engine_version") or ENGINE_VERSION
        ),
        "quality_flags": list(document.get("quality_flags", [])),
    }


def article_fields(document: dict[str, Any]) -> dict[str, Any]:
    envelope = document["envelope"]
    structure = envelope["document_structure"]["value"]
    purpose = envelope["communication_purpose"]["value"]
    purpose_rule = str(envelope["communication_purpose"].get("rule_id") or "")
    origin = envelope["information_origin"]["value"]
    if purpose_rule.startswith("envelope.purpose.earnings_call_v1:"): kind = "transcript"
    elif purpose == "explain_move": kind = "why_moving"
    elif origin == "analyst": kind = "analyst"
    elif origin == "regulator": kind = "regulatory"
    elif structure in {"market_overview", "reference_list"}: kind = "market"
    elif structure == "multi_subject_digest": kind = "multi"
    elif origin == "issuer": kind = "company"
    else: kind = "editorial"
    format_by_kind = {"transcript": "earnings_call_transcript", "why_moving": "why_moving", "analyst": "analyst_action", "regulatory": "regulatory_filing", "multi": "multi_company_coverage", "company": "company_announcement", "editorial": "editorial_coverage", "market": "general"}
    tickers = [str(row.get("ticker") or "") for row in document.get("entities", []) if row.get("ticker")]
    return {
        "news_kind": kind, "news_format": format_by_kind[kind], "news_origin": origin,
        "news_scope": "single_ticker" if len(tickers) == 1 else "multi_ticker" if tickers else "market_wide",
        "news_topics": sorted({str(row["concept_leaf"]) for row in document.get("statements", [])}),
        "is_company_news": origin == "issuer", "classification_confidence": 1.0,
        "classification_evidence": [f"news_synthesis:{envelope['communication_purpose']['rule_id']}"]
    }

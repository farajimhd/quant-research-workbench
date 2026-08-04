from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable


ENGINE_VERSION = "news_synthesis_engine_v1"


def load_news_synthesis(
    source_ids: list[str], *, query_rows: Callable[[str], list[dict[str, Any]]], quote: Callable[[str], str]
) -> dict[str, dict[str, Any]]:
    ids = sorted({value.strip() for value in source_ids if value.strip()})
    if not ids:
        return {}
    rows = query_rows(f"""
SELECT canonical_news_id,synthesis_json
FROM q_live.news_synthesis_v1 FINAL
WHERE engine_version={quote(ENGINE_VERSION)}
  AND canonical_news_id IN ({','.join(quote(value) for value in ids)})
ORDER BY updated_at_utc DESC
LIMIT 1 BY canonical_news_id,engine_version
FORMAT JSONEachRow""")
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        try: document = json.loads(str(row.get("synthesis_json") or "{}"))
        except json.JSONDecodeError: continue
        source_id = str(row.get("canonical_news_id") or "")
        output[source_id] = presentation_payload(document)
    return output


def presentation_payload(document: dict[str, Any]) -> dict[str, Any]:
    entities = {str(row["entity_id"]): row for row in document.get("entities", [])}
    statements = {str(row["statement_id"]): row for row in document.get("statements", [])}
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in document.get("participations", []): by_entity[str(row["entity_id"])].append(row)
    eligibility = {(str(row["entity_id"]), str(row["product"])): bool(row["eligible"]) for row in document.get("eligibility", [])}
    labels = []
    for view in document.get("issuer_views", []):
        entity_id = str(view["entity_id"]); entity = entities.get(entity_id, {}); parts = by_entity.get(entity_id, [])
        ids = [str(row["statement_id"]) for row in parts]
        concepts = sorted({str(statements[sid]["concept_leaf"]) for sid in ids if sid in statements})
        quotes = list(dict.fromkeys(str(statements[sid]["evidence_spans"][0]["quote"]) for sid in ids if sid in statements))
        score = int(view.get("positive_strength", 0)) - int(view.get("negative_strength", 0))
        ticker = str(entity.get("ticker") or "")
        labels.append({
            "content_role": document["envelope"]["communication_purpose"]["value"], "event_id": document["source_id"], "event_concepts": concepts,
            "event_tickers": [str(row.get("ticker") or "") for row in entities.values() if row.get("ticker")], "evidence_scope": "issuer_passages",
            "forecast_trigger_eligible": eligibility.get((entity_id, "forecast_trigger"), False), "reaction_evaluation_eligible": eligibility.get((entity_id, "reaction_study"), False),
            "issuer_history_context_eligible": eligibility.get((entity_id, "issuer_history"), False), "prior_primary_context_eligible": eligibility.get((entity_id, "issuer_history"), False),
            "episode_followup_eligible": document["envelope"]["communication_purpose"]["value"] in {"recap", "explain_move"},
            "issuer_role": next((str(row["semantic_role"]) for row in parts if row.get("semantic_role") != "none"), "affected_subject"),
            "labeling_version": ENGINE_VERSION, "modality": "deterministic", "quality_flags": document.get("quality_flags", []), "confidence": 1.0 if entity.get("identity_status") == "resolved" else 0.5,
            "source_type": document["envelope"]["document_structure"]["value"], "source_subtype": document["envelope"]["production_method"]["value"], "issuer_relationship": "direct",
            "scope": document["envelope"]["document_structure"]["value"], "semantic_direction_basis": concepts, "semantic_direction": view["composite_sentiment"],
            "semantic_evidence_text": " ".join(quotes), "semantic_score": score, "source_origin": document["envelope"]["information_origin"]["value"],
            "ticker": ticker, "time_orientation": "current", "unit_id": f"{document['source_id']}:{ticker}", "unit_role": "issuer_view",
        })
    return {"document": document, "labels": labels, "summary": summary(labels, document), "article_fields": article_fields(document)}


def article_fields(document: dict[str, Any]) -> dict[str, Any]:
    envelope = document["envelope"]
    structure = envelope["document_structure"]["value"]
    purpose = envelope["communication_purpose"]["value"]
    origin = envelope["information_origin"]["value"]
    if purpose == "explain_move": kind = "why_moving"
    elif origin == "analyst": kind = "analyst"
    elif origin == "regulator": kind = "regulatory"
    elif structure in {"market_overview", "reference_list"}: kind = "market"
    elif structure == "multi_subject_digest": kind = "multi"
    elif origin == "issuer": kind = "company"
    else: kind = "editorial"
    format_by_kind = {"why_moving": "why_moving", "analyst": "analyst_action", "regulatory": "regulatory_filing", "multi": "multi_company_coverage", "company": "company_announcement", "editorial": "editorial_coverage", "market": "general"}
    tickers = [str(row.get("ticker") or "") for row in document.get("entities", []) if row.get("ticker")]
    return {
        "news_kind": kind, "news_format": format_by_kind[kind], "news_origin": origin,
        "news_scope": "single_ticker" if len(tickers) == 1 else "multi_ticker" if tickers else "market_wide",
        "news_topics": sorted({str(row["concept_leaf"]) for row in document.get("statements", [])}),
        "is_company_news": origin == "issuer", "classification_confidence": 1.0,
        "classification_evidence": [f"news_synthesis:{envelope['communication_purpose']['rule_id']}"]
    }


def summary(labels: list[dict[str, Any]], document: dict[str, Any]) -> dict[str, Any] | None:
    if not labels: return None
    strongest = max(labels, key=lambda row: abs(float(row.get("semantic_score") or 0)))
    return {
        "classified": True, "content_role": document["envelope"]["communication_purpose"]["value"], "source_origin": document["envelope"]["information_origin"]["value"],
        "event_concepts": sorted({concept for row in labels for concept in row["event_concepts"]}), "semantic_direction": strongest["semantic_direction"], "semantic_score": strongest["semantic_score"],
        "forecast_trigger_eligible": any(row["forecast_trigger_eligible"] for row in labels), "reaction_evaluation_eligible": any(row["reaction_evaluation_eligible"] for row in labels),
        "issuer_history_context_eligible": any(row["issuer_history_context_eligible"] for row in labels), "prior_primary_context_eligible": any(row["prior_primary_context_eligible"] for row in labels),
        "episode_followup_eligible": any(row["episode_followup_eligible"] for row in labels), "label_count": len(labels), "issuer_count": len(labels), "labeling_version": ENGINE_VERSION,
        "quality_flags": document.get("quality_flags", []),
    }

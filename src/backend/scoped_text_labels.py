from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable


SCOPED_LABELING_VERSION = "scoped_text_labeling_v5"


def load_scoped_news_labels(
    source_ids: list[str],
    *,
    query_rows: Callable[[str], list[dict[str, Any]]],
    quote: Callable[[str], str],
    source_end: str = "",
    source_start: str = "",
    ticker: str = "",
) -> dict[str, list[dict[str, Any]]]:
    identities = sorted({value.strip() for value in source_ids if value.strip()})
    if not identities:
        return {}
    values = ",".join(quote(value) for value in identities)
    prewhere = _label_prewhere(
        "news", quote=quote, source_end=source_end, source_start=source_start,
        ticker=ticker,
    )
    sql = f"""
SELECT source_id,unit_id,ticker,unit_role,event_id,event_tickers,issuer_role,
       evidence_scope,semantic_evidence_text,content_role,source_origin,
       event_concepts,semantic_direction,semantic_score,
       forecast_trigger_eligible,reaction_evaluation_eligible,
       issuer_history_context_eligible,classification_json,labeling_version
FROM
(
    SELECT source_id,unit_id,ticker,unit_role,event_id,event_tickers,issuer_role,
           evidence_scope,semantic_evidence_text,content_role,source_origin,
           event_concepts,semantic_direction,semantic_score,
           forecast_trigger_eligible,reaction_evaluation_eligible,
           issuer_history_context_eligible,classification_json,labeling_version
    FROM q_live.scoped_text_labels_v5
    PREWHERE {prewhere}
    WHERE labeling_version={quote(SCOPED_LABELING_VERSION)}
      AND source_id IN ({values})
    ORDER BY updated_at_utc DESC
    LIMIT 1 BY corpus,ticker,source_timestamp,source_id,unit_id,labeling_version
)
ORDER BY source_id,forecast_trigger_eligible DESC,
         abs(semantic_score) DESC,ticker,unit_id
FORMAT JSONEachRow
"""
    rows = query_rows(sql)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("source_id") or "")].append(label_payload(row))
    return dict(grouped)


def load_scoped_sec_labels(
    source_ids: list[str],
    *,
    query_rows: Callable[[str], list[dict[str, Any]]],
    quote: Callable[[str], str],
    source_end: str = "",
    source_start: str = "",
    ticker: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Load document-scoped SEC labels without making filing reads depend on them."""
    return _load_scoped_labels(
        "sec", source_ids, query_rows=query_rows, quote=quote,
        source_end=source_end, source_start=source_start, ticker=ticker,
    )


def _load_scoped_labels(
    corpus: str,
    source_ids: list[str],
    *,
    query_rows: Callable[[str], list[dict[str, Any]]],
    quote: Callable[[str], str],
    source_end: str = "",
    source_start: str = "",
    ticker: str = "",
) -> dict[str, list[dict[str, Any]]]:
    identities = sorted({value.strip() for value in source_ids if value.strip()})
    if not identities:
        return {}
    values = ",".join(quote(value) for value in identities)
    prewhere = _label_prewhere(
        corpus, quote=quote, source_end=source_end, source_start=source_start,
        ticker=ticker,
    )
    sql = f"""
SELECT source_id,unit_id,ticker,unit_role,event_id,event_tickers,issuer_role,
       evidence_scope,semantic_evidence_text,content_role,source_origin,
       event_concepts,semantic_direction,semantic_score,
       forecast_trigger_eligible,reaction_evaluation_eligible,
       issuer_history_context_eligible,classification_json,labeling_version
FROM
(
    SELECT source_id,unit_id,ticker,unit_role,event_id,event_tickers,issuer_role,
           evidence_scope,semantic_evidence_text,content_role,source_origin,
           event_concepts,semantic_direction,semantic_score,
           forecast_trigger_eligible,reaction_evaluation_eligible,
           issuer_history_context_eligible,classification_json,labeling_version
    FROM q_live.scoped_text_labels_v5
    PREWHERE {prewhere}
    WHERE labeling_version={quote(SCOPED_LABELING_VERSION)}
      AND source_id IN ({values})
    ORDER BY updated_at_utc DESC
    LIMIT 1 BY corpus,ticker,source_timestamp,source_id,unit_id,labeling_version
)
ORDER BY source_id,forecast_trigger_eligible DESC,
         abs(semantic_score) DESC,ticker,unit_id
FORMAT JSONEachRow
"""
    rows = query_rows(sql)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("source_id") or "")].append(label_payload(row))
    return dict(grouped)


def _label_prewhere(
    corpus: str,
    *,
    quote: Callable[[str], str],
    source_end: str,
    source_start: str,
    ticker: str,
) -> str:
    conditions = [f"corpus={quote(corpus)}"]
    if ticker.strip():
        conditions.append(f"ticker={quote(ticker.strip().upper())}")
    if source_start.strip():
        conditions.append(
            "source_timestamp >= parseDateTime64BestEffort("
            f"{quote(source_start.strip())})"
        )
    if source_end.strip():
        conditions.append(
            "source_timestamp <= parseDateTime64BestEffort("
            f"{quote(source_end.strip())})"
        )
    return " AND ".join(conditions)


def label_payload(row: dict[str, Any]) -> dict[str, Any]:
    classification = _json_object(row.get("classification_json"))
    return {
        "unit_id": str(row.get("unit_id") or ""),
        "ticker": str(row.get("ticker") or ""),
        "unit_role": str(row.get("unit_role") or ""),
        "event_id": str(row.get("event_id") or ""),
        "event_tickers": _strings(row.get("event_tickers")),
        "issuer_role": str(row.get("issuer_role") or ""),
        "evidence_scope": str(row.get("evidence_scope") or ""),
        "semantic_evidence_text": str(row.get("semantic_evidence_text") or ""),
        "content_role": str(row.get("content_role") or ""),
        "source_origin": str(row.get("source_origin") or ""),
        "event_concepts": _strings(row.get("event_concepts")),
        "semantic_direction": str(row.get("semantic_direction") or "neutral"),
        "semantic_score": float(row.get("semantic_score") or 0.0),
        "forecast_trigger_eligible": bool(row.get("forecast_trigger_eligible")),
        "reaction_evaluation_eligible": bool(
            row.get("reaction_evaluation_eligible")
        ),
        "issuer_history_context_eligible": bool(
            row.get("issuer_history_context_eligible")
        ),
        "modality": str(classification.get("modality") or ""),
        "time_orientation": str(classification.get("time_orientation") or ""),
        "quality_flags": _strings(classification.get("quality_flags")),
        "confidence": float(classification.get("confidence") or 0.0),
        "source_type": str(classification.get("source_type") or ""),
        "source_subtype": str(classification.get("source_subtype") or ""),
        "issuer_relationship": str(
            classification.get("issuer_relationship") or ""
        ),
        "scope": str(classification.get("scope") or ""),
        "prior_primary_context_eligible": bool(
            classification.get("prior_primary_context_eligible")
        ),
        "episode_followup_eligible": bool(
            classification.get("episode_followup_eligible")
        ),
        "semantic_direction_basis": _string_list(
            classification.get("semantic_direction_basis")
        ),
        "labeling_version": str(
            row.get("labeling_version") or SCOPED_LABELING_VERSION
        ),
    }


def scoped_news_summary(
    labels: list[dict[str, Any]], *, ticker: str = ""
) -> dict[str, Any] | None:
    relevant = [
        row
        for row in labels
        if not ticker or str(row.get("ticker") or "").upper() == ticker.upper()
    ]
    if not relevant:
        relevant = labels
    if not relevant:
        return None
    primary = max(
        relevant,
        key=lambda row: (
            bool(row.get("forecast_trigger_eligible")),
            abs(float(row.get("semantic_score") or 0.0)),
            bool(row.get("reaction_evaluation_eligible")),
        ),
    )
    directions = {
        str(row.get("semantic_direction") or "neutral") for row in relevant
    }
    direction = next(iter(directions)) if len(directions) == 1 else "mixed"
    return {
        "content_role": primary.get("content_role") or "",
        "source_origin": primary.get("source_origin") or "",
        "semantic_direction": direction,
        "semantic_score": float(primary.get("semantic_score") or 0.0),
        "event_concepts": list(
            dict.fromkeys(
                concept
                for row in relevant
                for concept in _strings(row.get("event_concepts"))
            )
        )[:8],
        "forecast_trigger_eligible": any(
            bool(row.get("forecast_trigger_eligible")) for row in relevant
        ),
        "reaction_evaluation_eligible": any(
            bool(row.get("reaction_evaluation_eligible")) for row in relevant
        ),
        "issuer_history_context_eligible": any(
            bool(row.get("issuer_history_context_eligible")) for row in relevant
        ),
        "issuer_count": len(
            {str(row.get("ticker") or "") for row in relevant if row.get("ticker")}
        ),
        "label_count": len(relevant),
        "quality_flags": list(dict.fromkeys(
            flag
            for row in relevant
            for flag in _strings(row.get("quality_flags"))
        ))[:8],
        "classified": True,
        "labeling_version": SCOPED_LABELING_VERSION,
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    scalar = str(value or "").strip()
    return [scalar] if scalar else []

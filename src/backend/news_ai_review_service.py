from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable, Sequence

from research.mlops.clickhouse import sql_string


def load_news_ai_state(
    source_ids: Sequence[str], *, query_rows: Callable[[str], list[dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    ids = sorted({value for value in map(str, source_ids) if value})
    if not ids:
        return {}
    values = ",".join(sql_string(value) for value in ids)
    rows = query_rows(f"""
SELECT * FROM (
SELECT canonical_news_id,stage,forecast_eligibility,eligible_probability,threshold,
       model_release_id,created_at_utc,'' review_status,'' trigger_mode,'' requested_by,
       '' issuer_labels_json,'' review_model,0 review_cost_usd,0 review_latency_ms,'' review_error
FROM q_live.news_forecast_funnel_v1 FINAL WHERE canonical_news_id IN ({values})
UNION ALL
SELECT canonical_news_id,'' stage,'' forecast_eligibility,0 eligible_probability,0 threshold,
       '' model_release_id,updated_at_utc,status review_status,trigger_mode,requested_by,
       issuer_labels_json,model review_model,cost_usd review_cost_usd,latency_ms review_latency_ms,error review_error
FROM q_live.news_llm_issuer_review_v1 FINAL WHERE canonical_news_id IN ({values})
) AS state_rows
ORDER BY canonical_news_id,created_at_utc
FORMAT JSONEachRow
""")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = str(row["canonical_news_id"])
        target = result.setdefault(source_id, {"funnel": None, "review": {"status": "not_reviewed"}})
        if row.get("stage"):
            target["funnel"] = {
                "stage": row["stage"], "forecast_eligibility": row["forecast_eligibility"],
                "eligible_probability": row["eligible_probability"], "threshold": row["threshold"],
                "release_id": row["model_release_id"], "updated_at_utc": row["created_at_utc"],
            }
        if row.get("review_status"):
            labels = json.loads(row["issuer_labels_json"]) if row.get("issuer_labels_json") else None
            target["review"] = {
                "status": row["review_status"], "trigger_mode": row["trigger_mode"],
                "requested_by": row["requested_by"], "labels": labels,
                "model": row["review_model"], "cost_usd": row["review_cost_usd"],
                "latency_ms": row["review_latency_ms"], "error": row["review_error"],
                "updated_at_utc": row["created_at_utc"],
            }
    hypothesis_rows = query_rows(f"""
SELECT canonical_news_id,ticker,contract_version,hypothesis_json,provider,model,
       cost_usd,latency_ms,context_as_of_utc,created_at_utc
FROM q_live.news_market_hypothesis_v1 FINAL
WHERE canonical_news_id IN ({values})
ORDER BY canonical_news_id,ticker,created_at_utc
FORMAT JSONEachRow
""")
    for row in hypothesis_rows:
        target = result.setdefault(str(row["canonical_news_id"]), {"funnel": None, "review": {"status": "not_reviewed"}})
        target.setdefault("hypotheses", []).append({
            "ticker": row["ticker"], "contract_version": row["contract_version"],
            "prediction": json.loads(row["hypothesis_json"]), "provider": row["provider"],
            "model": row["model"], "cost_usd": row["cost_usd"], "latency_ms": row["latency_ms"],
            "context_as_of_utc": row["context_as_of_utc"], "created_at_utc": row["created_at_utc"],
        })
    return result


def request_news_review(canonical_news_id: str, published_at_utc: str, requested_by: str) -> dict[str, Any]:
    base = os.environ.get("TEXT_INTELLIGENCE_URL", "http://127.0.0.1:8804").rstrip("/")
    payload = {
        "canonical_news_id": canonical_news_id,
        "published_at_utc": published_at_utc,
        "requested_by": requested_by or "operator",
    }
    request = urllib.request.Request(
        f"{base}/news-review", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return json.loads(response.read().decode())

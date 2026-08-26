from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable, Sequence

from research.mlops.clickhouse import quote_ident, sql_string
from research.text_intelligence.sec_synthesis_v1 import ENGINE_VERSION


def load_sec_synthesis_state(
    accessions: Sequence[str],
    *,
    database: str,
    query_rows: Callable[[str], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    values = sorted({str(value) for value in accessions if value})
    if not values:
        return {}
    clause = ",".join(sql_string(value) for value in values)
    db = quote_ident(database)
    synthesis_rows = query_rows(f"""
SELECT accession_number,synthesis_json,updated_at_utc
FROM {db}.sec_synthesis_v1 FINAL
WHERE engine_version={sql_string(ENGINE_VERSION)} AND accession_number IN ({clause})
FORMAT JSONEachRow
""")
    result: dict[str, dict[str, Any]] = {
        str(row["accession_number"]): {
            "synthesis": json.loads(str(row["synthesis_json"])),
            "review": {"status": "not_reviewed"},
            "updated_at_utc": row.get("updated_at_utc"),
        }
        for row in synthesis_rows
    }
    review_rows = query_rows(f"""
SELECT accession_number,status,trigger_mode,requested_by,review_json,
       fundamental_direction,materiality_probability,forecast_relevance_probability,
       provider,model,cost_usd,latency_ms,error,updated_at_utc
FROM {db}.sec_llm_issuer_review_v1 FINAL
WHERE accession_number IN ({clause})
FORMAT JSONEachRow
""")
    for row in review_rows:
        accession = str(row["accession_number"])
        target = result.setdefault(accession, {"synthesis": None})
        target["review"] = {
            "status": row.get("status") or "not_reviewed",
            "trigger_mode": row.get("trigger_mode") or "manual",
            "requested_by": row.get("requested_by") or "",
            "result": json.loads(str(row["review_json"])) if row.get("review_json") else None,
            "fundamental_direction": row.get("fundamental_direction") or "",
            "materiality_probability": row.get("materiality_probability") or 0,
            "forecast_relevance_probability": row.get("forecast_relevance_probability") or 0,
            "provider": row.get("provider") or "", "model": row.get("model") or "",
            "cost_usd": row.get("cost_usd") or 0, "latency_ms": row.get("latency_ms") or 0,
            "error": row.get("error") or "", "updated_at_utc": row.get("updated_at_utc"),
        }
    return result


def request_sec_review(cik: str, accession_number: str, requested_by: str) -> dict[str, Any]:
    base = os.environ.get("TEXT_INTELLIGENCE_URL", "http://127.0.0.1:8804").rstrip("/")
    timeout_seconds = float(os.environ.get("TEXT_INTELLIGENCE_SEC_REVIEW_ADMISSION_TIMEOUT_SECONDS", "30"))
    if timeout_seconds <= 0:
        raise ValueError("TEXT_INTELLIGENCE_SEC_REVIEW_ADMISSION_TIMEOUT_SECONDS must be positive")
    payload = {"cik": cik, "accession_number": accession_number, "requested_by": requested_by or "operator"}
    request = urllib.request.Request(
        f"{base}/sec-review", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode())

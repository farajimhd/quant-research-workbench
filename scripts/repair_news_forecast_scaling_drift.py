from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "services" / "text-intelligence"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.text_intelligence.news_synthesis_v1.deepfm_serving import DeepFMServingRelease
from text_intelligence.config import IntelligenceConfig
from text_intelligence.forecast_review import ForecastReviewRuntime, ReviewRequest


BROKEN_RELEASE_ID = "news-deepfm-pre-august-v1"
REPAIRED_RELEASE_ID = "news-deepfm-pre-august-v1-scaling-repair-v2"
REPAIRED_FUNNEL_CONTRACT = "news_forecast_funnel_deepfm_only_serving_v3"
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1"
) / "scaling_repair_v2"

_RUNTIME: ForecastReviewRuntime | None = None
_CLIENT: ClickHouseHttpClient | None = None


def _client() -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(),
        default_clickhouse_password(), timeout_seconds=60,
    )


def _initialize_worker() -> None:
    global _CLIENT, _RUNTIME
    config = IntelligenceConfig.from_env()
    if config.forecast_release_manifest.name != "release_v2.json":
        raise ValueError("repair requires the versioned DeepFM v2 release manifest")
    _CLIENT = _client()
    _RUNTIME = ForecastReviewRuntime(config, _CLIENT, "q_live")
    _RUNTIME.release = DeepFMServingRelease(
        config.forecast_release_manifest, device=config.forecast_model_device,
    )
    if _RUNTIME.release.release_id != REPAIRED_RELEASE_ID:
        raise ValueError("repair worker loaded an unexpected DeepFM release")


def _repair_one(old: dict[str, Any]) -> dict[str, Any]:
    if _RUNTIME is None:
        raise RuntimeError("repair worker is not initialized")
    request = ReviewRequest(
        canonical_news_id=str(old["canonical_news_id"]),
        published_at_utc=str(old["published_at_utc"]),
        requested_by="deepfm-scaling-repair-v2",
    )
    source = _RUNTIME._load_source(request)
    new = _RUNTIME.process_funnel(source, deterministic={})
    return {
        "canonical_news_id": str(old["canonical_news_id"]),
        "published_at_utc": str(old["published_at_utc"]),
        "rendered_text_hash": str(new["rendered_text_hash"]),
        "old_contract_version": str(old["contract_version"]),
        "old_release_id": str(old["model_release_id"]),
        "old_release_hash": str(old["model_release_hash"]),
        "old_probability": float(old["eligible_probability"]),
        "old_eligibility": str(old["forecast_eligibility"]),
        "new_contract_version": str(new["contract_version"]),
        "new_release_id": str(new["model_release_id"]),
        "new_release_hash": str(new["model_release_hash"]),
        "new_probability": float(new["eligible_probability"]),
        "new_eligibility": str(new["forecast_eligibility"]),
        "corrected_at_utc": str(new["created_at_utc"]),
    }


def _affected_rows(client: ClickHouseHttpClient) -> list[dict[str, Any]]:
    return list(client.iter_json_each_row(f"""
SELECT canonical_news_id,
       toString(argMax(published_at_utc,created_at_utc)) published_at_utc,
       argMax(contract_version,created_at_utc) contract_version,
       argMax(model_release_id,created_at_utc) model_release_id,
       argMax(model_release_hash,created_at_utc) model_release_hash,
       argMax(eligible_probability,created_at_utc) eligible_probability,
       argMax(forecast_eligibility,created_at_utc) forecast_eligibility
FROM q_live.news_forecast_funnel_v1 FINAL
WHERE model_release_id='{BROKEN_RELEASE_ID}'
GROUP BY canonical_news_id
ORDER BY published_at_utc,canonical_news_id
FORMAT JSONEachRow
"""))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rescore every row produced by the inverted DeepFM serving transform",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    workers = max(1, min(16, int(args.workers)))
    IntelligenceConfig.from_env()
    client = _client()
    try:
        affected = _affected_rows(client)
    finally:
        client.close()
    if not affected:
        raise RuntimeError("no rows from the broken release were found")
    args.output_root.mkdir(parents=True, exist_ok=True)
    ledger_path = args.output_root / "CORRECTION_LEDGER.jsonl"
    report_path = args.output_root / "REPORT.json"
    if ledger_path.exists() or report_path.exists():
        raise FileExistsError("scaling repair ledger already exists")

    context = multiprocessing.get_context("spawn")
    corrected: list[dict[str, Any]] = []
    with context.Pool(workers, initializer=_initialize_worker) as pool:
        with ledger_path.open("x", encoding="utf-8", newline="\n") as handle:
            for index, row in enumerate(
                pool.imap_unordered(_repair_one, affected, chunksize=1), start=1,
            ):
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                corrected.append(row)
                if index % 100 == 0 or index == len(affected):
                    print(json.dumps({"corrected": index, "total": len(affected)}), flush=True)

    changed = [row for row in corrected if row["old_eligibility"] != row["new_eligibility"]]
    report = {
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "broken_release_id": BROKEN_RELEASE_ID,
        "repaired_release_id": REPAIRED_RELEASE_ID,
        "repaired_funnel_contract": REPAIRED_FUNNEL_CONTRACT,
        "affected_rows": len(affected),
        "corrected_rows": len(corrected),
        "eligibility_changed": len(changed),
        "eligible_to_ineligible": sum(
            row["old_eligibility"] == "eligible" and row["new_eligibility"] == "ineligible"
            for row in corrected
        ),
        "ineligible_to_eligible": sum(
            row["old_eligibility"] == "ineligible" and row["new_eligibility"] == "eligible"
            for row in corrected
        ),
        "downstream_reconciliation": {
            "current_projection": "latest funnel row now resolves to the repaired v3 contract",
            "historical_signal_occurrences": "preserved as immutable provenance; canonical_news_id links each occurrence to this correction ledger",
            "silent_rewrite": False,
        },
        "ledger": str(ledger_path),
    }
    _write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

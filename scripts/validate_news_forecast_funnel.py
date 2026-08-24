from __future__ import annotations

import json
import sys
from pathlib import Path

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
from text_intelligence.forecast_review import ForecastReviewRuntime


def main() -> None:
    config = IntelligenceConfig.from_env()
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=30
    )
    try:
        candidates = list(client.iter_json_each_row("""
SELECT canonical_news_id,toString(published_at_utc) published_at_utc,synthesis_json
FROM q_live.news_synthesis_v1 FINAL
PREWHERE updated_at_utc>=now64(6)-INTERVAL 7 DAY
ORDER BY updated_at_utc DESC LIMIT 200 FORMAT JSONEachRow
"""))
        candidate = next(
            (
                row
                for row in candidates
                if any(
                    item.get("product") == "forecast_trigger" and bool(item.get("eligible"))
                    for item in json.loads(row["synthesis_json"]).get("eligibility") or []
                )
            ),
            None,
        )
        if candidate is None:
            raise RuntimeError("no deterministic-eligible News source is available for validation")
        runtime = ForecastReviewRuntime(config, client, "q_live")
        from text_intelligence.forecast_review import ReviewRequest

        source = runtime._load_source(ReviewRequest(
            canonical_news_id=str(candidate["canonical_news_id"]),
            published_at_utc=str(candidate["published_at_utc"]),
            force=True,
        ))
        release = DeepFMServingRelease(config.forecast_release_manifest, device=config.forecast_model_device)
        scored = release.score(
            source,
            ticker_history=runtime._ticker_history(source),
            market_cap=runtime._market_cap_context(source),
        )
        print(json.dumps({
            "status": "passed",
            "source_id": source["source_id"],
            "published_at_utc": source["source_timestamp"],
            "release_id": scored["release_id"],
            "release_hash": scored["release_hash"],
            "eligible_probability": scored["eligible_probability"],
            "threshold": scored["threshold"],
            "forecast_eligibility": scored["forecast_eligibility"],
        }, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()

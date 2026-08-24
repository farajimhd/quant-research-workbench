from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=20
    )
    try:
        queries = {
            "funnel": "SELECT count() FROM q_live.news_forecast_funnel_v1",
            "review_latest": "SELECT count() FROM q_live.news_llm_issuer_review_v1 WHERE status='complete'",
            "review_history": "SELECT count() FROM q_live.news_llm_issuer_review_history_v1",
            "hypothesis_latest": "SELECT count() FROM q_live.news_market_hypothesis_v1",
            "hypothesis_history": "SELECT count() FROM q_live.news_market_hypothesis_history_v1",
        }
        counts = {name: int(client.execute(sql).strip() or "0") for name, sql in queries.items()}
        required = ("funnel", "review_latest", "review_history", "hypothesis_latest", "hypothesis_history")
        if any(counts.get(name, 0) < 1 for name in required):
            raise RuntimeError(f"News review persistence is incomplete: {counts}")
        print(json.dumps({"status": "passed", "counts": counts}, indent=2, sort_keys=True))
    finally:
        client.close()


if __name__ == "__main__":
    main()

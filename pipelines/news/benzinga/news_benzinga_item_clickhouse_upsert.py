from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.core.clickhouse_writer import NewsWriteConfig, write_news_pipeline_result  # noqa: E402
from pipelines.news.benzinga.core.item_pipeline import ItemPipelineOptions, process_benzinga_news_item  # noqa: E402
from pipelines.news.benzinga.core.url_policy import load_policy  # noqa: E402
from pipelines.news.benzinga.news_benzinga_normalize import stable_hash  # noqa: E402
from pipelines.news.benzinga.news_benzinga_url_policy import (  # noqa: E402
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.clickhouse import ClickHouseHttpClient  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process one raw Benzinga JSON item and upsert it into canonical news/ticker ClickHouse tables.")
    parser.add_argument("--raw-json", required=True)
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE") or "q_live")
    parser.add_argument("--normalized-table", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_TABLE") or "benzinga_news_normalized_v1")
    parser.add_argument("--ticker-table", default=os.environ.get("NEWS_BENZINGA_TICKER_TABLE") or "benzinga_news_ticker_v1")
    parser.add_argument("--text-limit-chars", type=int, default=50_000)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-ticker-change", action="store_true")
    parser.add_argument("--skip-table-validation", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    raw_path = Path(args.raw_json)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"raw JSON must contain an object: {raw_path}")
    policy = load_policy(args.policy_json)
    result = process_benzinga_news_item(
        payload,
        policy=policy,
        raw_artifact_path=str(raw_path),
        raw_payload_hash=stable_hash(json.dumps(payload, sort_keys=True, default=str)),
        options=ItemPipelineOptions(text_limit_chars=args.text_limit_chars),
    )
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    summary = write_news_pipeline_result(
        client,
        result,
        config=NewsWriteConfig(
            database=args.database,
            normalized_table=args.normalized_table,
            ticker_table=args.ticker_table,
            execute=args.execute,
            allow_ticker_change=args.allow_ticker_change,
            skip_table_validation=args.skip_table_validation,
        ),
    )
    print("=" * 96, flush=True)
    print("Benzinga item ClickHouse upsert", flush=True)
    print(f"raw_json={raw_path}", flush=True)
    print(f"target={args.database}.{args.normalized_table} + {args.database}.{args.ticker_table}", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["REAL_LIVE_CLICKHOUSE_WRITE_URL", "CLICKHOUSE_LIVE_STORAGE_POLICY"]), sort_keys=True), flush=True)
    print("summary=" + json.dumps(asdict(summary), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()

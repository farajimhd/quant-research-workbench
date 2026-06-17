from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig  # noqa: E402
from pipelines.news.benzinga.news_pipeline.live import run_live_ingest_cycle  # noqa: E402
from pipelines.news.benzinga.news_pipeline.provider import BenzingaProviderClient, BenzingaProviderConfig  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live Benzinga REST polling ingest using the reusable item-level news package.")
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit.")
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("NEWS_BENZINGA_LIVE_POLL_SECONDS") or "5"))
    parser.add_argument("--lookback-minutes", type=int, default=int(os.environ.get("NEWS_BENZINGA_LIVE_LOOKBACK_MINUTES") or "15"))
    parser.add_argument("--limit-items", type=int, default=None, help="Limit items per cycle. Use 0 to validate config without provider fetch.")
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--text-limit-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_TEXT_LIMIT_CHARS") or "50000"))
    parser.add_argument("--endpoint-url", default=os.environ.get("NEWS_BENZINGA_URL") or os.environ.get("NEWS_MASSIVE_BENZINGA_URL") or "https://api.massive.com/benzinga/v2/news")
    parser.add_argument("--api-key", default=os.environ.get("MASSIVE_API_KEY") or "")
    parser.add_argument("--page-limit", type=int, default=int(os.environ.get("NEWS_BENZINGA_PAGE_LIMIT") or "1000"))
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("NEWS_BENZINGA_MAX_PAGES") or "5"))
    parser.add_argument("--clickhouse-url", default=ClickHouseTargetConfig.from_env().url)
    parser.add_argument("--user", default=ClickHouseTargetConfig.from_env().user)
    parser.add_argument("--password", default=ClickHouseTargetConfig.from_env().password)
    parser.add_argument("--database", default=ClickHouseTargetConfig.from_env().database)
    parser.add_argument("--normalized-table", default=ClickHouseTargetConfig.from_env().normalized_table)
    parser.add_argument("--ticker-table", default=ClickHouseTargetConfig.from_env().ticker_table)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--insert-existing", action="store_true", help="Do not skip rows already present by canonical_news_id.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if args.limit_items != 0 and not args.api_key:
        raise RuntimeError("MASSIVE_API_KEY is required unless --limit-items 0 is used")
    pipeline_config = BenzingaPipelineConfig(
        policy_json=args.policy_json,
        text_limit_chars=args.text_limit_chars,
    )
    target = ClickHouseTargetConfig(
        url=args.clickhouse_url,
        user=args.user,
        password=args.password,
        database=args.database,
        normalized_table=args.normalized_table,
        ticker_table=args.ticker_table,
    )
    provider = BenzingaProviderClient(
        BenzingaProviderConfig(
            endpoint_url=args.endpoint_url,
            api_key=args.api_key or "not-used-for-limit-zero",
            page_limit=args.page_limit,
            max_pages=args.max_pages,
        )
    )
    print("=" * 96, flush=True)
    print("Benzinga live package ingest", flush=True)
    print(f"once={args.once} poll_seconds={args.poll_seconds} lookback_minutes={args.lookback_minutes}", flush=True)
    print(f"endpoint_url={args.endpoint_url}", flush=True)
    print(f"target={target.database}.{target.normalized_table} + {target.database}.{target.ticker_table}", flush=True)
    print(f"execute={args.execute} skip_existing={not args.insert_existing}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["MASSIVE_API_KEY", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)
    while True:
        summary = run_live_ingest_cycle(
            lookback_minutes=args.lookback_minutes,
            execute=args.execute,
            limit_items=args.limit_items,
            pipeline_config=pipeline_config,
            clickhouse_target=target,
            provider=provider,
            skip_existing=not args.insert_existing,
        )
        print("cycle=" + json.dumps(asdict(summary), sort_keys=True, default=str), flush=True)
        if args.once:
            return
        time.sleep(max(0.5, args.poll_seconds))


if __name__ == "__main__":
    main()

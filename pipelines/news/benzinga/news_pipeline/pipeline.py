from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.news.benzinga.core.clickhouse_writer import (
    NewsBatchWriteConfig,
    NewsBatchWriteSummary,
    NewsWriteConfig,
    NewsWriteSummary,
    write_many_news_pipeline_results,
    write_news_pipeline_result,
)
from pipelines.news.benzinga.core.contracts import NewsPipelineResult
from pipelines.news.benzinga.core.item_pipeline import ItemPipelineOptions, process_benzinga_news_item
from pipelines.news.benzinga.core.url_policy import load_policy
from pipelines.news.benzinga.news_benzinga_normalize import stable_hash, to_provider_rfc3339
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig
from research.mlops.clickhouse import ClickHouseHttpClient


@dataclass(frozen=True, slots=True)
class ProcessedNewsItem:
    result: NewsPipelineResult
    raw_json_path: str = ""


class BenzingaNewsPipeline:
    def __init__(self, config: BenzingaPipelineConfig | None = None) -> None:
        self.config = config or BenzingaPipelineConfig.from_env()
        self.policy = load_policy(self.config.policy_json)

    def process_payload(
        self,
        payload: dict[str, Any],
        *,
        raw_artifact_path: str = "",
        raw_payload_hash: str = "",
        downloaded_at_utc: datetime | None = None,
        enrichment_rows: list[dict[str, Any]] | None = None,
    ) -> ProcessedNewsItem:
        result = process_benzinga_news_item(
            payload,
            policy=self.policy,
            raw_artifact_path=raw_artifact_path,
            raw_payload_hash=raw_payload_hash or stable_hash(json.dumps(payload, sort_keys=True, default=str)),
            downloaded_at_utc=downloaded_at_utc,
            enrichment_rows=enrichment_rows,
            options=ItemPipelineOptions(
                text_limit_chars=self.config.text_limit_chars,
                max_enriched_text_chars_per_url=self.config.max_enriched_text_chars_per_url,
                max_enriched_urls_per_article=self.config.max_enriched_urls_per_article,
            ),
        )
        return ProcessedNewsItem(result=result, raw_json_path=raw_artifact_path)

    def process_raw_file(self, path: str | Path) -> ProcessedNewsItem:
        raw_path = Path(path)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"raw Benzinga file is not a JSON object: {raw_path}")
        return self.process_payload(
            payload,
            raw_artifact_path=str(raw_path),
            raw_payload_hash=stable_hash(json.dumps(payload, sort_keys=True, default=str)),
        )

    def write_one(
        self,
        processed: ProcessedNewsItem,
        *,
        target: ClickHouseTargetConfig | None = None,
        execute: bool = False,
        allow_ticker_change: bool = False,
        skip_table_validation: bool = False,
    ) -> NewsWriteSummary:
        target_cfg = target or ClickHouseTargetConfig.from_env()
        client = ClickHouseHttpClient(target_cfg.url, target_cfg.user, target_cfg.password)
        return write_news_pipeline_result(
            client,
            processed.result,
            config=NewsWriteConfig(
                database=target_cfg.database,
                normalized_table=target_cfg.normalized_table,
                ticker_table=target_cfg.ticker_table,
                execute=execute,
                allow_ticker_change=allow_ticker_change,
                skip_table_validation=skip_table_validation,
            ),
        )

    def write_many(
        self,
        processed: list[ProcessedNewsItem],
        *,
        target: ClickHouseTargetConfig | None = None,
        execute: bool = False,
        skip_existing: bool = True,
        skip_table_validation: bool = False,
    ) -> NewsBatchWriteSummary:
        target_cfg = target or ClickHouseTargetConfig.from_env()
        client = ClickHouseHttpClient(target_cfg.url, target_cfg.user, target_cfg.password)
        return write_many_news_pipeline_results(
            client,
            [item.result for item in processed],
            config=NewsBatchWriteConfig(
                database=target_cfg.database,
                normalized_table=target_cfg.normalized_table,
                ticker_table=target_cfg.ticker_table,
                execute=execute,
                skip_existing=skip_existing,
                skip_table_validation=skip_table_validation,
            ),
        )


def raw_downloaded_at_now() -> str:
    return to_provider_rfc3339(datetime.now(UTC))

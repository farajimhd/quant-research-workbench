from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.news.benzinga.core.clickhouse_writer import (
    NewsBatchWriteSummary,
    NewsWriteSummary,
)
from pipelines.news.benzinga.core.clickhouse_writer_v2 import (
    NewsV2TargetConfig,
    write_many_news_pipeline_results_v2,
    write_news_pipeline_result_v2,
)
from pipelines.news.benzinga.core.clickhouse_writer_body_v3 import (
    NewsBodyV3TargetConfig,
    write_many_news_pipeline_results_body_v3,
)
from pipelines.news.benzinga.core.contracts import NewsPipelineResult
from pipelines.news.benzinga.core.item_pipeline import ItemPipelineOptions, process_benzinga_news_item
from pipelines.news.benzinga.core.url_policy import load_policy
from pipelines.news.benzinga.news_benzinga_normalize import stable_hash, to_provider_rfc3339
from pipelines.news.benzinga.news_pipeline.config import BenzingaPipelineConfig, ClickHouseTargetConfig
from pipelines.news.benzinga.news_pipeline.enrichment import NewsEnrichmentBatch, NewsUrlEnricher
from research.mlops.clickhouse import ClickHouseHttpClient


@dataclass(frozen=True, slots=True)
class ProcessedNewsItem:
    result: NewsPipelineResult
    raw_json_path: str = ""


@dataclass(frozen=True, slots=True)
class EnrichedNewsItem:
    processed: ProcessedNewsItem
    enrichment: NewsEnrichmentBatch


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
        shadow_active, _shadow_warning = body_v3_shadow_state(self.config)
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
                build_body_v3=shadow_active,
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

    def process_payload_enriched(
        self,
        payload: dict[str, Any],
        *,
        enricher: NewsUrlEnricher,
        raw_artifact_path: str = "",
        raw_payload_hash: str = "",
        downloaded_at_utc: datetime | None = None,
    ) -> EnrichedNewsItem:
        """Run the identical discovery, acquisition and V2 render path everywhere."""
        initial = self.process_payload(
            payload,
            raw_artifact_path=raw_artifact_path,
            raw_payload_hash=raw_payload_hash,
            downloaded_at_utc=downloaded_at_utc,
        )
        enrichment = enricher.enrich_tasks(initial.result.url_resolution.fetch_tasks)
        if not initial.result.url_resolution.fetch_tasks:
            return EnrichedNewsItem(processed=initial, enrichment=enrichment)
        final = self.process_payload(
            payload,
            raw_artifact_path=raw_artifact_path,
            raw_payload_hash=raw_payload_hash,
            downloaded_at_utc=downloaded_at_utc,
            enrichment_rows=enrichment.rows,
        )
        return EnrichedNewsItem(processed=final, enrichment=enrichment)

    def process_raw_file_enriched(self, path: str | Path, *, enricher: NewsUrlEnricher) -> EnrichedNewsItem:
        raw_path = Path(path)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"raw Benzinga file is not a JSON object: {raw_path}")
        return self.process_payload_enriched(
            payload,
            enricher=enricher,
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
        client = news_v2_write_client(target_cfg)
        try:
            del allow_ticker_change  # v2 links are revision-bound and never mutated in place.
            return write_news_pipeline_result_v2(
                client,
                processed.result,
                config=NewsV2TargetConfig(
                    database=target_cfg.database,
                    event_table=target_cfg.event_table,
                    source_table=target_cfg.source_table,
                    block_table=target_cfg.block_table,
                    rendered_table=target_cfg.rendered_table,
                    ticker_table=target_cfg.rendered_ticker_table,
                    authority_table=target_cfg.render_authority_table,
                    execute=execute,
                    skip_table_validation=skip_table_validation,
                ),
            )
        finally:
            client.close()

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
        client = news_v2_write_client(target_cfg)
        try:
            del skip_existing  # ReplacingMergeTree makes identical revision writes idempotent.
            summary = write_many_news_pipeline_results_v2(
                client,
                [item.result for item in processed],
                config=NewsV2TargetConfig(
                    database=target_cfg.database,
                    event_table=target_cfg.event_table,
                    source_table=target_cfg.source_table,
                    block_table=target_cfg.block_table,
                    rendered_table=target_cfg.rendered_table,
                    ticker_table=target_cfg.rendered_ticker_table,
                    authority_table=target_cfg.render_authority_table,
                    execute=execute,
                    skip_table_validation=skip_table_validation,
                ),
            )
            shadow_active, shadow_warning = body_v3_shadow_state(self.config)
            if shadow_active:
                try:
                    write_many_news_pipeline_results_body_v3(
                        client,
                        [item.result for item in processed],
                        config=NewsBodyV3TargetConfig(
                            database=target_cfg.database,
                            execute=execute,
                            require_certified=True,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    summary = replace(
                        summary,
                        warnings=sorted(set(summary.warnings) | {
                            f"body_v3_shadow_failed:{type(exc).__name__}:{str(exc)[:180]}"
                        }),
                    )
            elif shadow_warning:
                summary = replace(
                    summary,
                    warnings=sorted(set(summary.warnings) | {shadow_warning}),
                )
            return summary
        finally:
            client.close()


def news_v2_write_client(target: ClickHouseTargetConfig) -> ClickHouseHttpClient:
    """Use one acknowledged HTTP session for each atomic v2 product group."""
    return ClickHouseHttpClient(
        target.url,
        target.user,
        target.password,
        persistent=True,
        default_query_params={"async_insert": 0, "wait_end_of_query": 1},
    )


def raw_downloaded_at_now() -> str:
    return to_provider_rfc3339(datetime.now(UTC))


def body_v3_shadow_state(config: BenzingaPipelineConfig) -> tuple[bool, str]:
    if not config.body_v3_shadow_enabled:
        return False, ""
    if not config.body_v3_shadow_end_utc:
        return False, "body_v3_shadow_disabled:missing_end_utc"
    value = config.body_v3_shadow_end_utc.replace("Z", "+00:00")
    try:
        end = datetime.fromisoformat(value)
    except ValueError:
        return False, "body_v3_shadow_disabled:invalid_end_utc"
    if end.tzinfo is None:
        return False, "body_v3_shadow_disabled:naive_end_utc"
    if datetime.now(UTC) >= end.astimezone(UTC):
        return False, "body_v3_shadow_expired"
    return True, ""

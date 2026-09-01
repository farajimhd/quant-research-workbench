from __future__ import annotations

from pipelines.news.benzinga.core.clickhouse_writer_body_v3 import (
    BLOCK_COLUMNS,
    EVENT_COLUMNS,
    LINEAGE_COLUMNS,
    RENDERED_COLUMNS,
    SOURCE_COLUMNS,
    TICKER_COLUMNS,
    NewsBodyV3TargetConfig,
    create_body_v3_tables,
    validate_body_v3_tables,
    write_many_news_pipeline_results_body_v3,
)
from pipelines.news.benzinga.core.clickhouse_writer import NewsBatchWriteSummary
from pipelines.news.benzinga.core.contracts import NewsPipelineResult
from pipelines.news.benzinga.news_benzinga_body_v4 import BODY_RENDERER_VERSION, body_purity_reasons
from research.mlops.clickhouse import ClickHouseHttpClient


DEFAULT_DATABASE = "q_live"
DEFAULT_EVENT_TABLE = "benzinga_news_event_v4"
DEFAULT_SOURCE_TABLE = "benzinga_news_source_v4"
DEFAULT_BLOCK_TABLE = "benzinga_news_block_v4"
DEFAULT_RENDERED_TABLE = "benzinga_news_rendered_v4"
DEFAULT_TICKER_TABLE = "benzinga_news_ticker_v4"
DEFAULT_LINEAGE_TABLE = "benzinga_news_body_lineage_v2"
DEFAULT_AUTHORITY_TABLE = "benzinga_news_body_authority_v1"


def body_v4_target_config(
    *,
    database: str = DEFAULT_DATABASE,
    execute: bool = False,
    require_certified: bool = False,
    skip_table_validation: bool = False,
) -> NewsBodyV3TargetConfig:
    return NewsBodyV3TargetConfig(
        database=database,
        event_table=DEFAULT_EVENT_TABLE,
        source_table=DEFAULT_SOURCE_TABLE,
        block_table=DEFAULT_BLOCK_TABLE,
        rendered_table=DEFAULT_RENDERED_TABLE,
        ticker_table=DEFAULT_TICKER_TABLE,
        lineage_table=DEFAULT_LINEAGE_TABLE,
        authority_table=DEFAULT_AUTHORITY_TABLE,
        renderer_version=BODY_RENDERER_VERSION,
        execute=execute,
        require_certified=require_certified,
        skip_table_validation=skip_table_validation,
    )


# The schemas and batch transport are version-neutral. These aliases keep the
# V4 caller explicit while preserving the already-tested writer implementation.
create_body_v4_tables = create_body_v3_tables
validate_body_v4_tables = validate_body_v3_tables


def write_many_news_pipeline_results_body_v4(
    client: ClickHouseHttpClient,
    results: list[NewsPipelineResult],
    *,
    config: NewsBodyV3TargetConfig,
) -> NewsBatchWriteSummary:
    for result in results:
        rendered = result.body_rendered_row
        if not rendered:
            raise RuntimeError(f"pipeline result lacks Body V4 rows: {result.canonical_news_id}")
        if str(rendered.get("renderer_version") or "") != BODY_RENDERER_VERSION:
            raise RuntimeError(f"pipeline result is not Body V4: {result.canonical_news_id}")
        reasons = body_purity_reasons(str(rendered.get("canonical_body_text") or ""))
        if reasons:
            raise RuntimeError(
                f"Body V4 purity rejected {result.canonical_news_id}: {','.join(reasons)}"
            )
    return write_many_news_pipeline_results_body_v3(client, results, config=config)

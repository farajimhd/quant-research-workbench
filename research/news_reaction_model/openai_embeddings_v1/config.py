from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


MODEL = "text-embedding-3-large"
DIMENSIONS = 3_072
EMBEDDING_VERSION = "news_openai_text_embedding_3_large_3072_v1"
TEXT_CONTRACT = "news_reaction_v7_publication_text_12000chars_8000tokens_v1"
HARD_MAX_COST_USD = Decimal("50.00")

# Published prices on 2026-07-22. The Batch price is the expected charge. The
# synchronous price is deliberately used for reservations so a request routed
# outside Batch still cannot make this pipeline exceed its configured ceiling.
BATCH_PRICE_USD_PER_MILLION = Decimal("0.13")
RESERVATION_PRICE_USD_PER_MILLION = Decimal("0.26")


def default_runtime_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_OPENAI_EMBEDDING_ROOT",
            r"D:\market-data\prepared\news_reaction_model\openai_embeddings_v1",
        )
    )


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    database: str = "market_sip_compact"
    source_table: str = "news_reaction_stock_state_dataset_v7"
    source_version: str = "news_reaction_stock_state_dataset_v7"
    news_database: str = "q_live"
    news_table: str = "benzinga_news_normalized_v1"
    embedding_table: str = "news_openai_embeddings_v1"
    item_table: str = "news_openai_embedding_items_v1"
    batch_table: str = "news_openai_embedding_batches_v1"
    model: str = MODEL
    dimensions: int = DIMENSIONS
    embedding_version: str = EMBEDDING_VERSION
    text_contract: str = TEXT_CONTRACT
    max_text_chars: int = 12_000
    max_input_tokens: int = 8_000
    max_request_tokens: int = 250_000
    max_request_inputs: int = 1_024
    # This fits the current Tier-1 3M-token Batch queue with safety headroom.
    max_batch_tokens: int = 2_500_000
    max_batch_inputs: int = 50_000
    max_inflight_batches: int = 1
    planner_workers: int = 4
    tokenizer_threads: int = 4
    clickhouse_threads: int = 4
    clickhouse_memory: str = "16G"
    # Long Batch runs must survive brief ClickHouse transport outages. Retries
    # apply only to read queries; durable writes remain single-attempt so an
    # ambiguous response can be reconciled from ReplacingMergeTree state.
    clickhouse_read_attempts: int = 8
    clickhouse_retry_delay_seconds: float = 2.0
    clickhouse_retry_max_delay_seconds: float = 30.0
    insert_rows: int = 64
    poll_seconds: int = 30
    start_date: str = "2019-01-01"
    end_date_exclusive: str = "2027-01-01"
    runtime_root: Path = field(default_factory=default_runtime_root)
    hard_max_cost_usd: Decimal = HARD_MAX_COST_USD
    batch_price_usd_per_million: Decimal = BATCH_PRICE_USD_PER_MILLION
    reservation_price_usd_per_million: Decimal = RESERVATION_PRICE_USD_PER_MILLION

    @property
    def input_root(self) -> Path:
        return self.runtime_root / "inputs"

    @property
    def output_root(self) -> Path:
        return self.runtime_root / "outputs"

    @property
    def status_path(self) -> Path:
        return self.runtime_root / "status.jsonl"

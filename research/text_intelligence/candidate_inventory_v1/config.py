from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig


INVENTORY_VERSION = "text_candidate_inventory_v1"
NORMALIZER_VERSION = "financial_phrase_normalizer_v4"


def default_runtime_root() -> Path:
    explicit = os.environ.get("TEXT_CANDIDATE_INVENTORY_ROOT")
    if explicit:
        return Path(explicit)
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "candidate_inventory_v1"
    )


@dataclass(frozen=True, slots=True)
class CandidateInventoryConfig:
    database: str = "q_live"
    news_event_table: str = "benzinga_news_event_v2"
    news_rendered_table: str = "benzinga_news_rendered_v2"
    news_authority_table: str = "benzinga_news_render_authority_v2"
    sec_filing_table: str = "sec_filing_v3"
    sec_document_table: str = "sec_filing_document_v3"
    sec_rendered_table: str = "sec_filing_text_rendered_v3"
    sources: tuple[str, ...] = ("news", "sec")
    start_date: str = "2010-01-01"
    end_date_exclusive: str = "2027-01-01"
    workers: int = 8
    news_page_size: int = 256
    sec_page_size: int = 16
    checkpoint_pages: int = 10
    min_ngram: int = 2
    max_ngram: int = 6
    unit_candidate_capacity: int = 100_000
    merged_candidate_capacity: int = 500_000
    max_unique_candidates_per_document: int = 250_000
    min_document_frequency: int = 5
    top_output_candidates: int = 100_000
    evidence_examples: int = 3
    evidence_chars: int = 240
    max_documents_per_source: int = 0
    clickhouse_timeout_seconds: float = 300.0
    runtime_root: Path = field(default_factory=default_runtime_root)

    def validate(self) -> None:
        invalid_sources = sorted(set(self.sources) - {"news", "sec"})
        if invalid_sources:
            raise ValueError(f"unsupported sources: {invalid_sources}")
        if not self.sources:
            raise ValueError("at least one source is required")
        if self.workers < 1:
            raise ValueError("workers must be positive")
        if not (1 <= self.min_ngram <= self.max_ngram <= 8):
            raise ValueError("ngram bounds must satisfy 1 <= min <= max <= 8")
        for name in (
            "news_page_size",
            "sec_page_size",
            "checkpoint_pages",
            "unit_candidate_capacity",
            "merged_candidate_capacity",
            "min_document_frequency",
            "top_output_candidates",
            "evidence_examples",
            "evidence_chars",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        if self.max_unique_candidates_per_document < 0:
            raise ValueError("max_unique_candidates_per_document cannot be negative")
        if self.max_documents_per_source < 0:
            raise ValueError("max_documents_per_source cannot be negative")

    @property
    def run_root(self) -> Path:
        source_key = "-".join(self.sources)
        limit_key = (
            f"limit-{self.max_documents_per_source}"
            if self.max_documents_per_source
            else "full"
        )
        return self.runtime_root / (
            f"{source_key}_{self.start_date}_{self.end_date_exclusive}"
            f"_n{self.min_ngram}-{self.max_ngram}"
            f"_norm-{NORMALIZER_VERSION.rsplit('_v', 1)[-1]}"
            f"_{limit_key}"
        )

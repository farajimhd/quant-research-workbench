from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class NewsArticleForClassification(BaseModel):
    source: str = ""
    provider_article_id: str = ""
    canonical_article_id: str = ""
    published_at: str = ""
    title: str = ""
    teaser: str = ""
    body_text: str = ""
    extracted_text: str = ""
    article_url: str = ""
    publisher_name: str = ""
    tickers: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    content_scope: str = ""
    scanner_relevance: str = ""
    model_relevance: str = ""
    catalyst_labels: list[str] = Field(default_factory=list)
    insight_tickers: list[str] = Field(default_factory=list)
    insight_sentiments: list[str] = Field(default_factory=list)
    insight_reasons: list[str] = Field(default_factory=list)

    def classification_text(self, max_chars: int) -> str:
        parts = [
            self.title.strip(),
            self.teaser.strip(),
            self.body_text.strip(),
            self.extracted_text.strip(),
        ]
        text = "\n\n".join(part for part in parts if part)
        return text[:max_chars]


class TickerImpact(BaseModel):
    ticker: str
    sentiment_label: str
    direction_score: float
    confidence: float
    rationale: str = ""


class IntelligenceResponse(BaseModel):
    status: str = "ok"
    processed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stack_version: str
    taxonomy_version: str
    prompt_version: str
    model_stack: list[str] = Field(default_factory=list)
    summary: str = ""
    sentiment_label: str = "neutral"
    sentiment_score: float = 0.0
    sentiment_confidence: float = 0.0
    event_type: str = "uncategorized"
    event_subtype: str = ""
    materiality_score: float = 0.0
    novelty_score: float = 0.0
    urgency_score: float = 0.0
    time_horizon: str = "unknown"
    affected_tickers: list[TickerImpact] = Field(default_factory=list)
    content_completeness: str = "unknown"
    evidence_basis: str = "unknown"
    labels: list[str] = Field(default_factory=list)
    rationale: str = ""
    raw_outputs: dict[str, Any] = Field(default_factory=dict)
    error: str = ""

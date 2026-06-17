from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class UrlPolicyEntry:
    policy_id: str
    policy_version: str
    provider: str
    match_type: str
    match_value: str
    action: str
    priority: int
    enabled: int
    reason: str
    source: str
    created_at_utc: str
    updated_at_utc: str


@dataclass(frozen=True, slots=True)
class UrlResolution:
    policy_version: str
    url_candidates: list[dict[str, Any]]
    fetch_tasks: list[dict[str, Any]]
    attachments: list[dict[str, Any]]
    action_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class NewsPipelineResult:
    provider_article_id: str
    canonical_news_id: str
    policy_version: str
    normalized_row: dict[str, Any]
    ticker_links: list[dict[str, Any]]
    url_resolution: UrlResolution
    warnings: list[str] = field(default_factory=list)

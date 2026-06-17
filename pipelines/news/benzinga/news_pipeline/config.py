from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pipelines.news.benzinga.core.clickhouse_writer import DEFAULT_DATABASE, DEFAULT_NORMALIZED_TABLE, DEFAULT_TICKER_TABLE
from pipelines.news.benzinga.news_benzinga_raw_download import DEFAULT_ENDPOINT
from pipelines.news.benzinga.news_benzinga_url_policy import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user


@dataclass(frozen=True, slots=True)
class ClickHouseTargetConfig:
    url: str
    user: str
    password: str
    database: str = DEFAULT_DATABASE
    normalized_table: str = DEFAULT_NORMALIZED_TABLE
    ticker_table: str = DEFAULT_TICKER_TABLE

    @classmethod
    def from_env(cls) -> "ClickHouseTargetConfig":
        return cls(
            url=default_clickhouse_url(),
            user=default_clickhouse_user(),
            password=default_clickhouse_password(),
            database=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE") or DEFAULT_DATABASE,
            normalized_table=os.environ.get("NEWS_BENZINGA_NORMALIZED_TABLE") or DEFAULT_NORMALIZED_TABLE,
            ticker_table=os.environ.get("NEWS_BENZINGA_TICKER_TABLE") or DEFAULT_TICKER_TABLE,
        )


@dataclass(frozen=True, slots=True)
class BenzingaPipelineConfig:
    policy_json: str = ""
    text_limit_chars: int = 50_000
    raw_root_win: Path = Path("D:/market-data/news-benzinga/raw")
    output_root_win: Path = Path("D:/market-data/prepared/benzinga_news_package")
    max_enriched_text_chars_per_url: int = 24_000
    max_enriched_urls_per_article: int = 5

    @classmethod
    def from_env(cls) -> "BenzingaPipelineConfig":
        return cls(
            policy_json=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "",
            text_limit_chars=int(os.environ.get("NEWS_BENZINGA_TEXT_LIMIT_CHARS") or "50000"),
            raw_root_win=Path(os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or "D:/market-data/news-benzinga/raw"),
            output_root_win=Path(os.environ.get("NEWS_BENZINGA_PACKAGE_OUTPUT_ROOT_WIN") or "D:/market-data/prepared/benzinga_news_package"),
            max_enriched_text_chars_per_url=int(os.environ.get("NEWS_BENZINGA_MAX_ENRICHED_TEXT_CHARS_PER_URL") or "24000"),
            max_enriched_urls_per_article=int(os.environ.get("NEWS_BENZINGA_MAX_ENRICHED_URLS_PER_ARTICLE") or "5"),
        )


@dataclass(frozen=True, slots=True)
class BenzingaProviderRuntimeConfig:
    endpoint_url: str = DEFAULT_ENDPOINT
    api_key: str = ""
    page_limit: int = 1_000
    max_pages: int = 1_000

    @classmethod
    def from_env(cls) -> "BenzingaProviderRuntimeConfig":
        return cls(
            endpoint_url=os.environ.get("NEWS_BENZINGA_URL") or os.environ.get("NEWS_MASSIVE_BENZINGA_URL") or DEFAULT_ENDPOINT,
            api_key=os.environ.get("MASSIVE_API_KEY") or "",
            page_limit=int(os.environ.get("NEWS_BENZINGA_PAGE_LIMIT") or "1000"),
            max_pages=int(os.environ.get("NEWS_BENZINGA_MAX_PAGES") or "1000"),
        )

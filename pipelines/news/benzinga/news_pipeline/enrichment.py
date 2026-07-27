from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from pipelines.news.benzinga.news_benzinga_url_download import DomainRateLimiter, download_row
from pipelines.news.benzinga.news_benzinga_url_extract import extract_row, read_artifact


@dataclass(frozen=True, slots=True)
class NewsEnrichmentConfig:
    """Shared live/historical URL acquisition contract.

    Downloaded HTML and PDF bytes are always preserved under ``artifact_root``.
    Rendering consumes those durable bytes; extracted text alone is not treated
    as the source authority.
    """

    artifact_root: Path
    enabled: bool = True
    per_domain_min_interval_seconds: float = 0.1
    timeout_seconds: float = 5.0
    max_html_bytes: int = 4_000_000
    max_pdf_bytes: int = 12_000_000
    max_retries: int = 0
    max_text_chars: int = 50_000


@dataclass(frozen=True, slots=True)
class NewsEnrichmentBatch:
    rows: list[dict[str, Any]]
    requested: int
    downloaded: int
    extracted: int
    failed: int


class NewsUrlEnricher:
    """Acquire and extract the URL tasks emitted by the item pipeline."""

    def __init__(
        self,
        config: NewsEnrichmentConfig,
        *,
        downloader: Callable[..., dict[str, Any]] = download_row,
        extractor: Callable[..., dict[str, Any]] = extract_row,
        artifact_reader: Callable[[Path, str], bytes] = read_artifact,
    ) -> None:
        self.config = config
        self._downloader = downloader
        self._extractor = extractor
        self._artifact_reader = artifact_reader
        self._rate_limiter = DomainRateLimiter(config.per_domain_min_interval_seconds)

    def enrich_tasks(self, tasks: Iterable[dict[str, Any]]) -> NewsEnrichmentBatch:
        task_rows = list(tasks)
        if not self.config.enabled or not task_rows:
            return NewsEnrichmentBatch(rows=[], requested=len(task_rows), downloaded=0, extracted=0, failed=0)

        args = SimpleNamespace(
            timeout_seconds=self.config.timeout_seconds,
            max_html_bytes=self.config.max_html_bytes,
            max_pdf_bytes=self.config.max_pdf_bytes,
            max_retries=self.config.max_retries,
            max_text_chars=self.config.max_text_chars,
        )
        rows: list[dict[str, Any]] = []
        downloaded = extracted = failed = 0
        for task in task_rows:
            download_result = self._downloader(task, args, self._rate_limiter, self.config.artifact_root)
            if download_result.get("status") != "downloaded":
                failed += 1
                rows.append(dict(download_result))
                continue
            downloaded += 1
            extraction = self._extractor(
                download_result,
                self.config.max_text_chars,
                self.config.max_pdf_bytes,
            )
            if extraction.get("status") == "extracted":
                extracted += 1
                if str(download_result.get("resolved_action") or "") == "fetch_html":
                    artifact_path = Path(str(download_result.get("artifact_path") or ""))
                    try:
                        raw_bytes = self._artifact_reader(
                            artifact_path,
                            str(download_result.get("artifact_compression") or ""),
                        )
                    except OSError as exc:
                        raise RuntimeError(
                            "Successful HTML extraction lacks its durable source artifact "
                            f"for {download_result.get('url_hash', '')}"
                        ) from exc
                    extraction["raw_html"] = raw_bytes.decode("utf-8", errors="replace")
            else:
                failed += 1
            rows.append(extraction)
        return NewsEnrichmentBatch(
            rows=rows,
            requested=len(task_rows),
            downloaded=downloaded,
            extracted=extracted,
            failed=failed,
        )

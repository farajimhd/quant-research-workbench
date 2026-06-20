from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.sec.edgar.sec_filing_text_extract_parts import (
    FilingParent,
    build_missing_parent_row,
    build_rows,
    parse_filing,
)
from pipelines.sec.edgar.sec_pipeline.feed import SecFeedItem, accession_text_url
from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient
from pipelines.sec.edgar.sec_pipeline.xbrl_live import LiveXbrlRows, SecLiveXbrlExtractor


@dataclass(frozen=True, slots=True)
class LiveFilingRows:
    filing_row: dict[str, Any]
    document_rows: list[dict[str, Any]]
    text_rows: list[dict[str, Any]]
    skip_rows: list[dict[str, Any]]
    xbrl_rows: LiveXbrlRows
    raw_path: Path


class SecLiveFilingPipeline:
    def __init__(self, *, http: SecHttpClient, raw_root_win: Path, min_text_chars: int = 40, max_text_chars: int = 5_000_000) -> None:
        self.http = http
        self.raw_root_win = raw_root_win
        self.min_text_chars = min_text_chars
        self.max_text_chars = max_text_chars
        self.xbrl_extractor = SecLiveXbrlExtractor(http=http)

    def process_feed_item(self, item: SecFeedItem, *, source_run_id: str) -> LiveFilingRows:
        url = accession_text_url(item.cik, item.accession_number)
        response = self.http.get(url)
        raw_path = self.write_raw(item, response.body)
        filing = parse_filing(response.body, f"{item.accession_number}.txt")
        inserted_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "")
        archive_date = (item.updated_at_utc or datetime.now(UTC)).date()
        archive_date_text = archive_date.isoformat()
        payload: dict[str, Any] = {
            "source_run_id": source_run_id,
            "min_text_chars": self.min_text_chars,
            "max_text_chars": self.max_text_chars,
            "sample_text_chars": 0,
        }
        filing_row, parent = build_missing_parent_row(
            payload,
            raw_path,
            archive_date,
            archive_date_text,
            raw_path.name,
            response.body,
            filing,
            inserted_at,
        )
        if item.filing_detail_url:
            filing_row["filing_detail_url"] = item.filing_detail_url
        document_rows: list[dict[str, Any]] = []
        text_rows: list[dict[str, Any]] = []
        skip_rows: list[dict[str, Any]] = []
        has_xbrl_payload = False
        for document in filing.get("documents") or []:
            doc_row, text_row, skip_row, _sample = build_rows(payload, raw_path, archive_date_text, raw_path.name, parent, document, inserted_at)
            document_rows.append(doc_row)
            if doc_row.get("document_role") == "xbrl_sidecar" or doc_row.get("content_format") == "xbrl":
                has_xbrl_payload = True
            if text_row:
                text_rows.append(text_row)
            if skip_row:
                skip_rows.append(skip_row)
        xbrl_rows = LiveXbrlRows()
        if has_xbrl_payload:
            xbrl_rows = self.xbrl_extractor.extract_for_accession(
                cik=parent.cik,
                accession_number=parent.accession_number,
                source_run_id=source_run_id,
                inserted_at=inserted_at,
            )
        return LiveFilingRows(
            filing_row=filing_row,
            document_rows=document_rows,
            text_rows=text_rows,
            skip_rows=skip_rows,
            xbrl_rows=xbrl_rows,
            raw_path=raw_path,
        )

    def write_raw(self, item: SecFeedItem, raw: bytes) -> Path:
        dt = item.updated_at_utc or datetime.now(UTC)
        root = self.raw_root_win / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{item.accession_number}.txt"
        path.write_bytes(raw)
        return path

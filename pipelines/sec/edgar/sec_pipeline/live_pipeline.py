from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.sec.edgar.sec_filing_text_extract_parts import (
    FilingParent,
    build_missing_parent_row,
    build_rows,
    parse_filing,
    sec_document_url,
    sec_filing_detail_url,
)
from pipelines.sec.edgar.sec_pipeline.feed import SecFeedItem, accession_text_url
from pipelines.sec.edgar.sec_pipeline.entities import build_entity_rows
from pipelines.sec.edgar.sec_pipeline.archive_accession import build_archive_accession_row
from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient
from pipelines.sec.edgar.sec_pipeline.revision import parse_pac_event, source_revision
from pipelines.sec.edgar.sec_pipeline.submissions import SecSubmissionFiling, SecSubmissionsClient
from pipelines.sec.edgar.sec_pipeline.xbrl_live import LiveXbrlRows, SecLiveXbrlExtractor


@dataclass(frozen=True, slots=True)
class LiveFilingRows:
    filing_row: dict[str, Any]
    entity_rows: list[dict[str, Any]]
    archive_accession_rows: list[dict[str, Any]]
    document_rows: list[dict[str, Any]]
    text_source_rows: list[dict[str, Any]]
    text_rows: list[dict[str, Any]]
    skip_rows: list[dict[str, Any]]
    pac_rows: list[dict[str, Any]]
    xbrl_rows: LiveXbrlRows
    raw_path: Path


class SecLiveFilingPipeline:
    def __init__(
        self,
        *,
        http: SecHttpClient,
        raw_root_win: Path,
        min_text_chars: int = 40,
        max_text_chars: int = 0,
        submissions_cache_entries: int = 512,
        submissions_cache_max_age_seconds: float = 3600.0,
        xbrl_payload_cache_entries: int = 32,
        xbrl_payload_cache_max_age_seconds: float = 3600.0,
        xbrl_missing_cik_cache_entries: int = 5_000,
    ) -> None:
        self.http = http
        self.raw_root_win = raw_root_win
        self.min_text_chars = min_text_chars
        self.max_text_chars = max_text_chars
        self.submissions = SecSubmissionsClient(
            http=http,
            max_cache_entries=submissions_cache_entries,
            max_cache_age_seconds=submissions_cache_max_age_seconds,
        )
        self.xbrl_extractor = SecLiveXbrlExtractor(
            http=http,
            max_payload_cache_entries=xbrl_payload_cache_entries,
            max_payload_cache_age_seconds=xbrl_payload_cache_max_age_seconds,
            max_missing_cik_cache_entries=xbrl_missing_cik_cache_entries,
        )

    def process_feed_item(self, item: SecFeedItem, *, source_run_id: str) -> LiveFilingRows:
        url = accession_text_url(item.cik, item.accession_number)
        response = self.http.get(url)
        raw_path = self.write_raw(item, response.body)
        filing = parse_filing(response.body, f"{item.accession_number}.txt")
        inserted_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        archive_date = (item.updated_at_utc or datetime.now(UTC)).date()
        archive_date_text = archive_date.isoformat()
        source_sha = hashlib.sha256(response.body).hexdigest()
        pac_event = parse_pac_event(
            response.body.decode("latin-1", errors="replace"),
            archive_date=archive_date,
            archive_member=raw_path.name,
            archive_path=raw_path,
            source_content_sha256=source_sha,
        )
        revision = source_revision(
            archive_date=archive_date,
            archive_member=raw_path.name,
            archive_path=raw_path,
            source_content_sha256=source_sha,
            pac_event=pac_event,
            occurrence_at_utc=item.updated_at_utc,
        )
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
        submission = self.submissions.fetch_recent_filing(cik=parent.cik, accession_number=parent.accession_number)
        if submission is not None:
            filing_row, parent = apply_submission_metadata(filing_row, parent, submission)
        document_rows: list[dict[str, Any]] = []
        text_source_rows: list[dict[str, Any]] = []
        text_rows: list[dict[str, Any]] = []
        skip_rows: list[dict[str, Any]] = []
        pac_rows = pac_event.rows(source_run_id=source_run_id, inserted_at=inserted_at) if pac_event else []
        entity_rows = [] if pac_event else build_entity_rows(
            entities=filing["entities"],
            filing_id=parent.filing_id,
            accession_number=parent.accession_number,
            primary_cik=parent.cik,
            source_archive_date=archive_date_text,
            source_archive_member=raw_path.name,
            source_archive_path=str(raw_path),
            source_header_sha256=filing["header_sha256"],
            revision=revision,
            source_run_id=source_run_id,
            inserted_at=inserted_at,
        )
        archive_accession_rows = [] if pac_event else [
            build_archive_accession_row(
                filing=filing,
                entities=filing["entities"],
                primary_cik=parent.cik,
                source_archive_date=archive_date_text,
                source_archive_member=raw_path.name,
                source_archive_path=str(raw_path),
                source_header_sha256=filing["header_sha256"],
                source_content_sha256=source_sha,
                document_count=len(filing.get("documents") or []),
                header_text=filing["header_text"],
                revision=revision,
                source_run_id=source_run_id,
                inserted_at=inserted_at,
                source_kind="live_accession_text",
            )
        ]
        has_xbrl_payload = False
        for document in [] if pac_event else (filing.get("documents") or []):
            doc_row, text_source_row, text_row, skip_row, _sample = build_rows(
                payload, raw_path, archive_date_text, raw_path.name, parent, document, inserted_at, revision=revision
            )
            document_rows.append(doc_row)
            if doc_row.get("document_role") == "xbrl_sidecar" or doc_row.get("content_format") == "xbrl":
                has_xbrl_payload = True
            if text_source_row:
                text_source_rows.append(text_source_row)
            if text_row:
                text_rows.append(text_row)
            if skip_row:
                skip_rows.append(skip_row)
        xbrl_rows = LiveXbrlRows()
        if has_xbrl_payload or (submission is not None and submission.has_xbrl):
            xbrl_rows = self.xbrl_extractor.extract_for_accession(
                cik=parent.cik,
                accession_number=parent.accession_number,
                source_run_id=source_run_id,
                inserted_at=inserted_at,
            )
        return LiveFilingRows(
            filing_row=filing_row,
            entity_rows=entity_rows,
            archive_accession_rows=archive_accession_rows,
            document_rows=document_rows,
            text_source_rows=text_source_rows,
            text_rows=text_rows,
            skip_rows=skip_rows,
            pac_rows=pac_rows,
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

    def cache_stats(self) -> dict[str, int]:
        return {
            **self.submissions.cache_stats(),
            **self.xbrl_extractor.cache_stats(),
        }


def apply_submission_metadata(
    filing_row: dict[str, Any],
    parent: FilingParent,
    submission: SecSubmissionFiling,
) -> tuple[dict[str, Any], FilingParent]:
    primary_document = submission.primary_document or filing_row.get("primary_document") or ""
    filing_detail_url = sec_filing_detail_url(parent.cik, parent.accession_number_compact)
    primary_document_url = sec_document_url(parent.cik, parent.accession_number_compact, primary_document) if primary_document else ""
    form_type = submission.form_type or str(filing_row.get("form_type") or "")
    accepted_at_utc = submission.accepted_at_utc or str(filing_row.get("accepted_at_utc") or "")
    filing_row.update(
        {
            "company_name": submission.entity_name or filing_row.get("company_name"),
            "form_type": form_type,
            "filing_date": submission.filing_date or filing_row.get("filing_date"),
            "report_date": submission.report_date or filing_row.get("report_date"),
            "accepted_at_utc": accepted_at_utc or filing_row.get("accepted_at_utc"),
            "acceptance_datetime_raw": submission.acceptance_datetime_raw or filing_row.get("acceptance_datetime_raw"),
            "accepted_at_source": "submissions_recent" if submission.accepted_at_utc else filing_row.get("accepted_at_source"),
            "primary_document": primary_document or None,
            "primary_document_url": primary_document_url or filing_row.get("primary_document_url"),
            "filing_detail_url": filing_detail_url or filing_row.get("filing_detail_url"),
            "filing_size": submission.filing_size if submission.filing_size is not None else filing_row.get("filing_size"),
            "items": submission.items or filing_row.get("items"),
            "text_status": "live_text_extracted",
        }
    )
    return filing_row, FilingParent(
        filing_id=parent.filing_id,
        accession_number=parent.accession_number,
        accession_number_compact=parent.accession_number_compact,
        cik=parent.cik,
        form_type=form_type,
        accepted_at_utc=accepted_at_utc,
        primary_document=primary_document,
        primary_document_url=primary_document_url,
        filing_detail_url=filing_detail_url,
    )

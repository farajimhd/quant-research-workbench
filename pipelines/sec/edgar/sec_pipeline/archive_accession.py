from __future__ import annotations

import re
from typing import Any

from pipelines.sec.edgar.sec_pipeline.entities import FilingEntity
from pipelines.sec.edgar.sec_pipeline.revision import SourceRevision


def build_archive_accession_row(
    *,
    filing: dict[str, Any],
    entities: list[FilingEntity],
    primary_cik: str,
    source_archive_date: str,
    source_archive_member: str,
    source_archive_path: str,
    source_header_sha256: str,
    source_content_sha256: str,
    document_count: int,
    header_text: str,
    revision: SourceRevision,
    source_run_id: str,
    inserted_at: str,
    source_kind: str,
) -> dict[str, Any]:
    accession = str(filing["accession_number"])
    return {
        "accession_number": accession,
        "accession_number_compact": accession.replace("-", ""),
        "primary_cik": primary_cik,
        "entity_ciks": sorted({entity.cik for entity in entities}),
        "form_type": str(filing.get("form_type") or ""),
        "filing_date": filing.get("filing_date") or None,
        "acceptance_datetime_raw": filing.get("acceptance_datetime_raw") or None,
        "document_count": max(0, int(document_count)),
        "public_document_count": max(0, _public_document_count(header_text)),
        "private_to_public": 1 if re.search(r"<PRIVATE-TO-PUBLIC(?:>|\s)", header_text, flags=re.I) else 0,
        "source_kind": source_kind,
        "source_archive_date": source_archive_date,
        "source_archive_member": source_archive_member,
        "source_archive_path": source_archive_path or None,
        "source_header_sha256": source_header_sha256,
        "source_content_sha256": source_content_sha256,
        "source_version_key": revision.source_version_key,
        "source_revision_at": revision.source_revision_at,
        "source_revision_rank": revision.source_revision_rank,
        "source_revision_kind": revision.source_revision_kind,
        "pac_event_id": revision.pac_event_id or None,
        "source_run_id": source_run_id,
        "inserted_at": inserted_at,
    }


def _public_document_count(header_text: str) -> int:
    match = re.search(r"^\s*PUBLIC DOCUMENT COUNT\s*:\s*(\d+)", header_text, flags=re.I | re.M)
    if not match:
        match = re.search(r"<PUBLIC-DOCUMENT-COUNT>\s*(\d+)", header_text, flags=re.I)
    return int(match.group(1)) if match else 0

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pipelines.sec.edgar.sec_pipeline.revision import SourceRevision


SECTION_ROLES = {
    "SUBJECT-COMPANY": "subject_company",
    "ISSUER": "issuer",
    "FILER": "filer",
    "REPORTING-OWNER": "reporting_owner",
    "FILED-BY": "filed_by",
}
PRIMARY_ROLE_PRIORITY = ("issuer", "subject_company", "filer", "reporting_owner", "filed_by", "submission_entity")
_SECTION_START = re.compile(
    r"<(SUBJECT-COMPANY|ISSUER|FILER|REPORTING-OWNER|FILED-BY)>\s*",
    flags=re.IGNORECASE,
)
_DATA_START = re.compile(r"<(COMPANY-DATA|OWNER-DATA)>\s*", flags=re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FilingEntity:
    cik: str
    role: str
    name: str
    section_ordinal: int


def parse_filing_entities(header_text: str) -> list[FilingEntity]:
    """Parse every SEC submission entity section without using the accession prefix."""
    sections = list(_SECTION_START.finditer(header_text))
    entities: list[FilingEntity] = []
    seen: set[tuple[str, str]] = set()
    for ordinal, match in enumerate(sections, start=1):
        end = sections[ordinal].start() if ordinal < len(sections) else len(header_text)
        block = header_text[match.end() : end]
        role = SECTION_ROLES[match.group(1).upper()]
        for data_block in _slice_data_blocks(block) or [block]:
            cik = normalize_cik(_tag_value(data_block, "CIK"))
            if not cik or cik == "0000000000" or (role, cik) in seen:
                continue
            seen.add((role, cik))
            entities.append(
                FilingEntity(
                    cik=cik,
                    role=role,
                    name=_tag_value(data_block, "CONFORMED-NAME"),
                    section_ordinal=ordinal,
                )
            )
    if not entities:
        cik = normalize_cik(
            _tag_value(header_text, "CENTRAL-INDEX-KEY")
            or _header_value(header_text, "CENTRAL INDEX KEY")
            or _tag_value(header_text, "CIK")
        )
        if cik:
            entities.append(
                FilingEntity(
                    cik=cik,
                    role="submission_entity",
                    name=_header_value(header_text, "COMPANY CONFORMED NAME") or _tag_value(header_text, "CONFORMED-NAME"),
                    section_ordinal=0,
                )
            )
    return entities


def primary_filing_entity(entities: Iterable[FilingEntity]) -> FilingEntity | None:
    values = list(entities)
    for role in PRIMARY_ROLE_PRIORITY:
        match = next((entity for entity in values if entity.role == role), None)
        if match is not None:
            return match
    return None


def build_entity_rows(
    *,
    entities: Iterable[FilingEntity],
    filing_id: str | None,
    accession_number: str,
    primary_cik: str,
    source_archive_date: str,
    source_archive_member: str,
    source_archive_path: str,
    source_header_sha256: str,
    revision: SourceRevision,
    source_run_id: str,
    inserted_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in entities:
        relationship_id = hashlib.sha256(
            f"sec-filing-entity-v3\0{accession_number}\0{entity.role}\0{entity.cik}".encode()
        ).hexdigest()
        rows.append(
            {
                "relationship_id": relationship_id,
                "filing_id": filing_id,
                "accession_number": accession_number,
                "accession_number_compact": accession_number.replace("-", ""),
                "primary_cik": primary_cik,
                "entity_cik": entity.cik,
                "entity_role": entity.role,
                "entity_name": entity.name or None,
                "source_section_ordinal": entity.section_ordinal,
                "source_archive_date": source_archive_date,
                "source_archive_member": source_archive_member,
                "source_archive_path": source_archive_path or None,
                "source_header_sha256": source_header_sha256,
                "source_version_key": revision.source_version_key,
                "source_revision_at": revision.source_revision_at,
                "source_revision_rank": revision.source_revision_rank,
                "source_revision_kind": revision.source_revision_kind,
                "pac_event_id": revision.pac_event_id or None,
                "source_run_id": source_run_id,
                "inserted_at": inserted_at,
            }
        )
    return rows


def _slice_data_blocks(section: str) -> list[str]:
    starts = list(_DATA_START.finditer(section))
    return [
        section[match.end() : starts[index + 1].start() if index + 1 < len(starts) else len(section)]
        for index, match in enumerate(starts)
    ]


def _tag_value(text: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}>\s*([^\n\r<]+)", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _header_value(text: str, label: str) -> str:
    match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.*?)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def normalize_cik(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    return digits.zfill(10)[-10:] if digits else ""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable


PAC_TIMESTAMP_FORMATS = ("%Y%m%d%H%M%S", "%Y%m%d%H%M%S%f")
REVISION_TIE_RANGE = 1_000_000_000


@dataclass(frozen=True, slots=True)
class PacDocumentChange:
    sequence_number: int
    document_name: str
    document_type: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class PacEvent:
    accession_number: str
    cik: str
    correction_timestamp_raw: str
    correction_order_key: int
    filing_date: str
    date_as_of_change: str
    form_type: str
    filing_deleted: bool
    document_changes: tuple[PacDocumentChange, ...]
    source_archive_date: str
    source_archive_member: str
    source_archive_path: str
    source_content_sha256: str

    @property
    def event_id(self) -> str:
        return deterministic_hash(
            "sec-pac-event-v2",
            self.accession_number,
            self.correction_timestamp_raw,
            self.source_archive_date,
            self.source_archive_member,
            self.source_content_sha256,
        )

    def rows(self, *, source_run_id: str, inserted_at: str) -> list[dict[str, Any]]:
        changes: Iterable[PacDocumentChange | None] = self.document_changes or (None,)
        rows: list[dict[str, Any]] = []
        for change in changes:
            change_dict = asdict(change) if change else {}
            rows.append(
                {
                    "pac_event_id": deterministic_hash(self.event_id, str(change_dict)),
                    "accession_number": self.accession_number,
                    "cik": self.cik,
                    "correction_timestamp_raw": self.correction_timestamp_raw,
                    "correction_order_key": self.correction_order_key,
                    "filing_date": self.filing_date or None,
                    "date_as_of_change": self.date_as_of_change or None,
                    "form_type": self.form_type,
                    "action": pac_action(self, change),
                    "filing_deleted": 1 if self.filing_deleted else 0,
                    "sequence_number": int(change.sequence_number) if change else 0,
                    "document_name": change.document_name if change else "",
                    "document_type": change.document_type if change else "",
                    "document_deleted": 1 if change and change.deleted else 0,
                    "source_archive_date": self.source_archive_date,
                    "source_archive_member": self.source_archive_member,
                    "source_archive_path": self.source_archive_path,
                    "source_content_sha256": self.source_content_sha256,
                    "source_run_id": source_run_id,
                    "inserted_at": inserted_at,
                }
            )
        return rows


@dataclass(frozen=True, slots=True)
class SourceRevision:
    source_version_key: str
    source_revision_at: str
    source_revision_rank: int
    source_revision_kind: str
    pac_event_id: str = ""

    def apply(self, row: dict[str, Any]) -> dict[str, Any]:
        row.update(
            {
                "source_version_key": self.source_version_key,
                "source_revision_at": self.source_revision_at,
                "source_revision_rank": self.source_revision_rank,
                "source_revision_kind": self.source_revision_kind,
                "pac_event_id": self.pac_event_id or None,
            }
        )
        return row


def parse_pac_event(
    decoded_submission: str,
    *,
    archive_date: date,
    archive_member: str,
    archive_path: Path | str,
    source_content_sha256: str,
) -> PacEvent | None:
    header = decoded_submission.split("<DOCUMENT>", 1)[0]
    if not (has_tag(header, "CORRECTION") or has_tag(header, "DELETION")):
        return None
    timestamp_raw, timestamp_order = parse_pac_timestamp(first_tag(header, "TIMESTAMP"), archive_date)
    changes: list[PacDocumentChange] = []
    for index, block in enumerate(re.findall(r"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", decoded_submission, flags=re.S | re.I), start=1):
        changes.append(
            PacDocumentChange(
                sequence_number=parse_int(first_tag(block, "SEQUENCE")) or index,
                document_name=clean(first_tag(block, "FILENAME")),
                document_type=clean(first_tag(block, "TYPE")).upper(),
                deleted=has_tag(block, "DELETION"),
            )
        )
    return PacEvent(
        accession_number=normalize_accession(first_tag(header, "ACCESSION-NUMBER") or Path(archive_member).stem),
        cik=normalize_cik(first_tag(header, "CIK")),
        correction_timestamp_raw=timestamp_raw,
        correction_order_key=timestamp_order,
        filing_date=parse_sec_date(first_tag(header, "FILING-DATE")),
        date_as_of_change=parse_sec_date(first_tag(header, "DATE-OF-FILING-DATE-CHANGE")),
        form_type=clean(first_tag(header, "TYPE")).upper(),
        filing_deleted=has_tag(header, "DELETION"),
        document_changes=tuple(changes),
        source_archive_date=archive_date.isoformat(),
        source_archive_member=archive_member,
        source_archive_path=str(archive_path),
        source_content_sha256=source_content_sha256,
    )


def source_revision(
    *,
    archive_date: date | str,
    archive_member: str,
    archive_path: Path | str,
    source_content_sha256: str,
    pac_event: PacEvent | None = None,
    occurrence_sequence: int = 0,
    occurrence_at_utc: datetime | None = None,
) -> SourceRevision:
    archive_day = archive_date if isinstance(archive_date, date) else date.fromisoformat(str(archive_date)[:10])
    revision_at = (
        occurrence_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if occurrence_at_utc is not None
        else f"{archive_day.isoformat()} 00:00:00.000"
    )
    kind = "pac" if pac_event else ("live_feed_occurrence" if occurrence_at_utc is not None else "archive_occurrence")
    if pac_event:
        within_day_order = pac_event.correction_order_key
    elif occurrence_at_utc is not None:
        occurrence = occurrence_at_utc.astimezone(UTC)
        within_day_order = (occurrence.hour * 3600 + occurrence.minute * 60 + occurrence.second) * 10_000 + occurrence.microsecond // 1000
    else:
        within_day_order = max(0, int(occurrence_sequence))
    base_seconds = int(datetime.combine(archive_day, datetime.min.time(), tzinfo=UTC).timestamp())
    tie = min(within_day_order, REVISION_TIE_RANGE - 1)
    rank = base_seconds * REVISION_TIE_RANGE + tie
    key = deterministic_hash(
        "sec-source-version-v2",
        archive_day.isoformat(),
        str(within_day_order),
        str(archive_path).replace("\\", "/").lower(),
        archive_member.lstrip("./").lower(),
        source_content_sha256,
    )
    return SourceRevision(key, revision_at, rank, kind, pac_event.event_id if pac_event else "")


def candidate_precedence(row: dict[str, Any]) -> tuple[int, int, int, str]:
    """Return deterministic SEC occurrence authority; ingestion time is never considered."""
    rank = int(row.get("source_revision_rank") or 0)
    if not rank:
        archive_day = date.fromisoformat(str(row.get("source_archive_date"))[:10])
        rank = int(datetime.combine(archive_day, datetime.min.time(), tzinfo=UTC).timestamp()) * REVISION_TIE_RANGE
    deleted = 1 if row.get("filing_deleted") or row.get("document_deleted") else 0
    nonempty = 1 if int(row.get("source_text_byte_count") or row.get("byte_size") or 0) > 0 else 0
    return rank, deleted, nonempty, str(row.get("source_version_key") or row.get("content_sha256") or "")


def select_authoritative_candidate(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = list(rows)
    return max(candidates, key=candidate_precedence) if candidates else None


def pac_action(event: PacEvent, change: PacDocumentChange | None) -> str:
    if event.filing_deleted:
        return "filing_deleted"
    if change and change.deleted:
        return "document_deleted"
    if change and change.document_type:
        return "document_type_changed"
    if event.filing_date or event.date_as_of_change:
        return "filing_date_or_header_changed"
    return "header_corrected"


def parse_pac_timestamp(value: str, fallback_date: date) -> tuple[str, int]:
    """Parse the SEC PAC timestamp as an order-only local value; SEC does not define its timezone."""
    compact = re.sub(r"\D", "", value or "")
    for fmt in PAC_TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(compact, fmt)
            seconds = parsed.hour * 3600 + parsed.minute * 60 + parsed.second
            millis = parsed.microsecond // 1000
            return compact, seconds * 10_000 + millis
        except ValueError:
            continue
    return fallback_date.strftime("%Y%m%d") + "000000", 0


def first_tag(text: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}>\s*([^\r\n<]*)", text, flags=re.I)
    return clean(match.group(1)) if match else ""


def has_tag(text: str, tag: str) -> bool:
    return bool(re.search(rf"<{re.escape(tag)}>(?:\s|<|$)", text, flags=re.I))


def parse_sec_date(value: str) -> str:
    compact = re.sub(r"\D", "", value or "")
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}" if len(compact) >= 8 else ""


def normalize_accession(value: str) -> str:
    compact = re.sub(r"\D", "", value or "")
    return f"{compact[:10]}-{compact[10:12]}-{compact[12:18]}" if len(compact) >= 18 else clean(value)


def normalize_cik(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits.zfill(10) if digits else ""


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def deterministic_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()

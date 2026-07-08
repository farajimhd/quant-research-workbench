from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient, SecHttpError


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


@dataclass(frozen=True, slots=True)
class SecSubmissionFiling:
    cik: str
    entity_name: str
    accession_number: str
    accession_number_compact: str
    form_type: str
    filing_date: str | None
    report_date: str | None
    acceptance_datetime_raw: str | None
    accepted_at_utc: str | None
    primary_document: str | None
    primary_doc_description: str | None
    filing_size: int | None
    items: str | None
    is_xbrl: bool
    is_inline_xbrl: bool
    source_content_sha256: str

    @property
    def has_xbrl(self) -> bool:
        return self.is_xbrl or self.is_inline_xbrl


class SecSubmissionsClient:
    def __init__(self, *, http: SecHttpClient, max_cache_entries: int = 512, max_cache_age_seconds: float = 3600.0) -> None:
        self.http = http
        self.max_cache_entries = max(0, int(max_cache_entries))
        self.max_cache_age_seconds = max(0.0, float(max_cache_age_seconds))
        self._payload_cache: OrderedDict[str, tuple[dict[str, Any], str, float]] = OrderedDict()
        self._cache_lock = threading.RLock()

    def fetch_recent_filing(self, *, cik: str, accession_number: str) -> SecSubmissionFiling | None:
        try:
            payload, source_sha = self.fetch_payload(cik=cik)
        except SecHttpError as exc:
            if exc.status == 404:
                return None
            raise
        return find_recent_filing(payload, source_sha=source_sha, accession_number=accession_number)

    def fetch_payload(self, *, cik: str) -> tuple[dict[str, Any], str]:
        import hashlib

        cik_text = str(cik).zfill(10)
        now = time.monotonic()
        with self._cache_lock:
            self._prune_expired_locked(now)
            cached = self._payload_cache.get(cik_text)
            if cached is not None:
                self._payload_cache.move_to_end(cik_text)
        if cached is not None:
            payload, source_sha, _cached_at = cached
            return payload, source_sha
        response = self.http.get(SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))
        source_sha = hashlib.sha256(response.body).hexdigest()
        payload = json.loads(response.body.decode("utf-8", errors="replace"))
        if self.max_cache_entries > 0:
            with self._cache_lock:
                self._payload_cache[cik_text] = (payload, source_sha, time.monotonic())
                self._payload_cache.move_to_end(cik_text)
                while len(self._payload_cache) > self.max_cache_entries:
                    self._payload_cache.popitem(last=False)
        return payload, source_sha

    def cache_stats(self) -> dict[str, int]:
        with self._cache_lock:
            self._prune_expired_locked(time.monotonic())
            entries = len(self._payload_cache)
        return {
            "submissions_cache_entries": entries,
            "submissions_cache_limit": self.max_cache_entries,
            "submissions_cache_max_age_seconds": int(self.max_cache_age_seconds),
        }

    def _prune_expired_locked(self, now: float) -> None:
        if self.max_cache_age_seconds <= 0:
            return
        expired = [
            cik
            for cik, (_payload, _source_sha, cached_at) in self._payload_cache.items()
            if now - cached_at > self.max_cache_age_seconds
        ]
        for cik in expired:
            self._payload_cache.pop(cik, None)


def find_recent_filing(payload: dict[str, Any], *, source_sha: str, accession_number: str) -> SecSubmissionFiling | None:
    recent = payload.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict):
        return None
    accessions = recent.get("accessionNumber")
    if not isinstance(accessions, list):
        return None
    try:
        index = [str(item) for item in accessions].index(accession_number)
    except ValueError:
        return None
    cik = str(payload.get("cik") or "").zfill(10)
    return SecSubmissionFiling(
        cik=cik,
        entity_name=clean_string(payload.get("name")),
        accession_number=clean_string(value_at(recent, "accessionNumber", index)),
        accession_number_compact=clean_string(value_at(recent, "accessionNumber", index)).replace("-", ""),
        form_type=clean_string(value_at(recent, "form", index)).upper(),
        filing_date=nullable_string(value_at(recent, "filingDate", index)),
        report_date=nullable_string(value_at(recent, "reportDate", index)),
        acceptance_datetime_raw=nullable_string(value_at(recent, "acceptanceDateTime", index)),
        accepted_at_utc=parse_acceptance_datetime(value_at(recent, "acceptanceDateTime", index)),
        primary_document=nullable_string(value_at(recent, "primaryDocument", index)),
        primary_doc_description=nullable_string(value_at(recent, "primaryDocDescription", index)),
        filing_size=int_or_none(value_at(recent, "size", index)),
        items=nullable_string(value_at(recent, "items", index)),
        is_xbrl=parse_bool(value_at(recent, "isXBRL", index)),
        is_inline_xbrl=parse_bool(value_at(recent, "isInlineXBRL", index)),
        source_content_sha256=source_sha,
    )


def value_at(payload: dict[str, Any], key: str, index: int) -> Any:
    values = payload.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def parse_acceptance_datetime(value: Any) -> str | None:
    text = clean_string(value)
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return format_utc_datetime64_json(parsed)
        except ValueError:
            pass
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 14:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}T{digits[8:10]}:{digits[10:12]}:{digits[12:14]}.000000000Z"
    return None


def format_utc_datetime64_json(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def clean_string(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def nullable_string(value: Any) -> str | None:
    text = clean_string(value)
    return text or None


def int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

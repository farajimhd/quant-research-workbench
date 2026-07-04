from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient, SecHttpError


COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


@dataclass(slots=True)
class LiveXbrlRows:
    concept_rows: list[dict[str, Any]] = field(default_factory=list)
    company_fact_rows: list[dict[str, Any]] = field(default_factory=list)
    frame_rows: list[dict[str, Any]] = field(default_factory=list)
    frame_observation_rows: list[dict[str, Any]] = field(default_factory=list)
    source_content_sha256: str = ""
    fetched: bool = False
    matched_facts: int = 0
    companyfacts_status: str = "not_requested"
    companyfacts_error: str = ""


class SecLiveXbrlExtractor:
    def __init__(
        self,
        *,
        http: SecHttpClient,
        max_payload_cache_entries: int = 32,
        max_payload_cache_age_seconds: float = 3600.0,
        max_missing_cik_cache_entries: int = 5_000,
    ) -> None:
        self.http = http
        self.max_payload_cache_entries = max(0, int(max_payload_cache_entries))
        self.max_payload_cache_age_seconds = max(0.0, float(max_payload_cache_age_seconds))
        self.max_missing_cik_cache_entries = max(0, int(max_missing_cik_cache_entries))
        self._payload_cache: OrderedDict[str, tuple[dict[str, Any], str, float]] = OrderedDict()
        self._missing_ciks: OrderedDict[str, None] = OrderedDict()
        self._cache_lock = threading.RLock()

    def extract_for_accession(self, *, cik: str, accession_number: str, source_run_id: str, inserted_at: str) -> LiveXbrlRows:
        return self.extract_for_accessions(cik=cik, accession_numbers={accession_number}, source_run_id=source_run_id, inserted_at=inserted_at)

    def extract_for_accessions(self, *, cik: str, accession_numbers: set[str], source_run_id: str, inserted_at: str) -> LiveXbrlRows:
        cik_text = str(cik).zfill(10)
        now = time.monotonic()
        with self._cache_lock:
            self._prune_expired_payloads_locked(now)
            if cik_text in self._missing_ciks:
                self._missing_ciks.move_to_end(cik_text)
                return LiveXbrlRows(fetched=True, companyfacts_status="missing_404")
            cached = self._payload_cache.get(cik_text)
            if cached is not None:
                self._payload_cache.move_to_end(cik_text)
        if cached is None:
            url = COMPANYFACTS_URL.format(cik=cik_text)
            try:
                response = self.http.get(url)
            except SecHttpError as exc:
                if exc.status == 404:
                    self._remember_missing_cik(cik_text)
                    return LiveXbrlRows(
                        fetched=True,
                        companyfacts_status="missing_404",
                        companyfacts_error=f"SEC companyfacts endpoint returned 404 for CIK{cik_text}",
                    )
                raise
            source_sha = hashlib.sha256(response.body).hexdigest()
            payload = json.loads(response.body.decode("utf-8", errors="replace"))
            self._remember_payload(cik_text, payload, source_sha)
        else:
            payload, source_sha, _cached_at = cached
        return extract_companyfacts_payload(
            payload,
            cik=cik,
            accession_numbers=accession_numbers,
            source_run_id=source_run_id,
            inserted_at=inserted_at,
            source_content_sha256=source_sha,
        )

    def _remember_payload(self, cik_text: str, payload: dict[str, Any], source_sha: str) -> None:
        if self.max_payload_cache_entries <= 0:
            return
        with self._cache_lock:
            self._payload_cache[cik_text] = (payload, source_sha, time.monotonic())
            self._payload_cache.move_to_end(cik_text)
            while len(self._payload_cache) > self.max_payload_cache_entries:
                self._payload_cache.popitem(last=False)

    def _remember_missing_cik(self, cik_text: str) -> None:
        if self.max_missing_cik_cache_entries <= 0:
            return
        with self._cache_lock:
            self._missing_ciks[cik_text] = None
            self._missing_ciks.move_to_end(cik_text)
            while len(self._missing_ciks) > self.max_missing_cik_cache_entries:
                self._missing_ciks.popitem(last=False)

    def cache_stats(self) -> dict[str, int]:
        with self._cache_lock:
            self._prune_expired_payloads_locked(time.monotonic())
            payload_entries = len(self._payload_cache)
            missing_entries = len(self._missing_ciks)
        return {
            "xbrl_payload_cache_entries": payload_entries,
            "xbrl_payload_cache_limit": self.max_payload_cache_entries,
            "xbrl_payload_cache_max_age_seconds": int(self.max_payload_cache_age_seconds),
            "xbrl_missing_cik_cache_entries": missing_entries,
            "xbrl_missing_cik_cache_limit": self.max_missing_cik_cache_entries,
        }

    def _prune_expired_payloads_locked(self, now: float) -> None:
        if self.max_payload_cache_age_seconds <= 0:
            return
        expired = [
            cik
            for cik, (_payload, _source_sha, cached_at) in self._payload_cache.items()
            if now - cached_at > self.max_payload_cache_age_seconds
        ]
        for cik in expired:
            self._payload_cache.pop(cik, None)


def extract_companyfacts_payload(
    payload: dict[str, Any],
    *,
    cik: str,
    accession_numbers: set[str],
    source_run_id: str,
    inserted_at: str,
    source_content_sha256: str,
) -> LiveXbrlRows:
    entity_name = clean_string(payload.get("entityName"))
    accessions = {clean_string(item) for item in accession_numbers if clean_string(item)}
    rows = LiveXbrlRows(source_content_sha256=source_content_sha256, fetched=True)
    rows.companyfacts_status = "available"
    seen_concepts: set[tuple[str, str]] = set()
    seen_frames: set[tuple[str, str, str, str]] = set()
    facts_root = payload.get("facts")
    if not isinstance(facts_root, dict):
        return rows
    for taxonomy, taxonomy_payload in facts_root.items():
        if not isinstance(taxonomy_payload, dict):
            continue
        for tag, tag_payload in taxonomy_payload.items():
            if not isinstance(tag_payload, dict):
                continue
            label = nullable_string(tag_payload.get("label"))
            description = nullable_string(tag_payload.get("description"))
            units = tag_payload.get("units")
            if not isinstance(units, dict):
                continue
            taxonomy_text = clean_string(taxonomy)
            tag_text = clean_string(tag)
            for unit_code, facts in units.items():
                if not isinstance(facts, list):
                    continue
                unit_text = clean_string(unit_code)
                for fact in facts:
                    if not isinstance(fact, dict):
                        continue
                    accession = clean_string(fact.get("accn"))
                    if accession not in accessions:
                        continue
                    value = float_or_none(fact.get("val"))
                    period_end = nullable_date(fact.get("end"))
                    if value is None or period_end is None:
                        continue
                    rows.matched_facts += 1
                    concept_key = (taxonomy_text, tag_text)
                    if concept_key not in seen_concepts:
                        seen_concepts.add(concept_key)
                        rows.concept_rows.append(
                            {
                                "concept_id": deterministic_id("sec-xbrl-concept", taxonomy_text, tag_text),
                                "taxonomy": taxonomy_text,
                                "tag": tag_text,
                                "concept_label": label,
                                "concept_description": description,
                                "first_observed_at_utc": filed_at(fact),
                                "last_observed_at_utc": filed_at(fact) or inserted_at,
                                "source_run_id": source_run_id,
                                "source_content_sha256": source_content_sha256,
                                "inserted_at": inserted_at,
                            }
                        )
                    rows.company_fact_rows.append(
                        {
                            "company_fact_id": deterministic_id(
                                "sec-xbrl-company-fact",
                                cik,
                                taxonomy_text,
                                tag_text,
                                unit_text,
                                str(fact.get("start") or ""),
                                str(fact.get("end") or ""),
                                accession,
                                str(fact.get("frame") or ""),
                            ),
                            "issuer_id": None,
                            "cik": str(cik).zfill(10),
                            "taxonomy": taxonomy_text,
                            "tag": tag_text,
                            "unit_code": unit_text,
                            "fiscal_year": int_or_none(fact.get("fy")),
                            "fiscal_period": nullable_string(fact.get("fp")),
                            "filed_at_utc": filed_at(fact),
                            "period_end_date": period_end,
                            "value": value,
                            "form_type": nullable_string(fact.get("form")),
                            "accession_number": accession,
                            "recorded_at_utc": inserted_at,
                            "source_run_id": source_run_id,
                            "source_content_sha256": source_content_sha256,
                            "inserted_at": inserted_at,
                        }
                    )
                    frame = clean_string(fact.get("frame"))
                    if frame:
                        frame_key = (taxonomy_text, tag_text, unit_text, frame)
                        frame_id = deterministic_id("sec-xbrl-frame", taxonomy_text, tag_text, unit_text, frame)
                        if frame_key not in seen_frames:
                            seen_frames.add(frame_key)
                            rows.frame_rows.append(
                                {
                                    "frame_id": frame_id,
                                    "taxonomy": taxonomy_text,
                                    "tag": tag_text,
                                    "unit_code": unit_text,
                                    "calendar_period_code": frame,
                                    "recorded_at_utc": inserted_at,
                                    "source_run_id": source_run_id,
                                    "source_content_sha256": source_content_sha256,
                                    "inserted_at": inserted_at,
                                }
                            )
                        rows.frame_observation_rows.append(
                            {
                                "frame_observation_id": deterministic_id(
                                    "sec-xbrl-frame-observation",
                                    frame_id,
                                    cik,
                                    accession,
                                    taxonomy_text,
                                    tag_text,
                                    unit_text,
                                    frame,
                                    period_end,
                                ),
                                "frame_id": frame_id,
                                "taxonomy": taxonomy_text,
                                "tag": tag_text,
                                "unit_code": unit_text,
                                "calendar_period_code": frame,
                                "issuer_id": None,
                                "cik": str(cik).zfill(10),
                                "entity_name": entity_name,
                                "location_code": None,
                                "period_end_date": period_end,
                                "value": value,
                                "accession_number": accession,
                                "recorded_at_utc": inserted_at,
                                "source_run_id": source_run_id,
                                "source_content_sha256": source_content_sha256,
                                "inserted_at": inserted_at,
                            }
                        )
    return rows


def deterministic_id(*parts: Any) -> str:
    text = json.dumps([str(part) for part in parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def nullable_date(value: Any) -> str | None:
    text = clean_string(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def filed_at(fact: dict[str, Any]) -> str | None:
    value = nullable_date(fact.get("filed"))
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC).isoformat(timespec="milliseconds").replace("+00:00", "")

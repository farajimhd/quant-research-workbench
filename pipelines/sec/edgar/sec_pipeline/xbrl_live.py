from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient


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


class SecLiveXbrlExtractor:
    def __init__(self, *, http: SecHttpClient) -> None:
        self.http = http

    def extract_for_accession(self, *, cik: str, accession_number: str, source_run_id: str, inserted_at: str) -> LiveXbrlRows:
        url = COMPANYFACTS_URL.format(cik=str(cik).zfill(10))
        response = self.http.get(url)
        source_sha = hashlib.sha256(response.body).hexdigest()
        payload = json.loads(response.body.decode("utf-8", errors="replace"))
        entity_name = clean_string(payload.get("entityName"))
        accession = clean_string(accession_number)
        rows = LiveXbrlRows(source_content_sha256=source_sha, fetched=True)
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
                        if not isinstance(fact, dict) or clean_string(fact.get("accn")) != accession:
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
                                    "source_content_sha256": source_sha,
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
                                "source_content_sha256": source_sha,
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
                                        "source_content_sha256": source_sha,
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
                                    "source_content_sha256": source_sha,
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

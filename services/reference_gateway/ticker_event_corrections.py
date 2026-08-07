from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_CORRECTIONS_PATH = Path(__file__).resolve().parent / "curated" / "ticker_event_corrections_v1.json"
CORRECTION_SOURCE_SYSTEM = "curated_sec_ticker_correction_v1"


@dataclass(frozen=True, slots=True)
class TickerEventCorrection:
    correction_id: str
    current_ticker: str
    composite_figi: str
    share_class_figi: str
    cik: str
    provider_event_signature: tuple[tuple[str, str], ...]
    canonical_timeline: tuple[tuple[str, str], ...]
    evidence_url: str
    evidence_accession_number: str
    evidence_summary: str
    source_content_sha256: str


def load_ticker_event_corrections(path: Path = DEFAULT_CORRECTIONS_PATH) -> tuple[TickerEventCorrection, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"ticker-event correction file must contain a nonempty JSON array: {path}")
    corrections = tuple(_validate_correction(dict(item)) for item in raw)
    keys = [(item.composite_figi, item.share_class_figi) for item in corrections]
    if len(keys) != len(set(keys)):
        raise ValueError("ticker-event correction file contains duplicate FIGI identities")
    return corrections


def correction_for_entity(entity: Any, corrections: tuple[TickerEventCorrection, ...]) -> TickerEventCorrection | None:
    composite_figi = str(getattr(entity, "composite_figi", "") or "").strip().upper()
    share_class_figi = str(getattr(entity, "share_class_figi", "") or "").strip().upper()
    matches = [
        item for item in corrections
        if item.composite_figi == composite_figi and item.share_class_figi == share_class_figi
    ]
    if len(matches) > 1:
        raise RuntimeError(f"multiple ticker-event corrections match {composite_figi}/{share_class_figi}")
    if not matches:
        return None
    correction = matches[0]
    actual_ticker = str(getattr(entity, "current_ticker", "") or "").strip().upper()
    actual_cik = str(getattr(entity, "cik", "") or "").strip()
    if actual_ticker != correction.current_ticker or actual_cik != correction.cik:
        raise RuntimeError(
            f"ticker-event correction identity drift for {correction.correction_id}: "
            f"ticker={actual_ticker!r} cik={actual_cik!r}"
        )
    return correction


def validate_provider_signature(correction: TickerEventCorrection, events: list[dict[str, Any]]) -> None:
    actual = tuple(sorted(_ticker_change_signature(events)))
    expected_defect = tuple(sorted(correction.provider_event_signature))
    expected_canonical = tuple(sorted(correction.canonical_timeline))
    if actual not in {expected_defect, expected_canonical}:
        raise RuntimeError(
            f"provider ticker-event signature drift for {correction.correction_id}: "
            f"expected {expected_defect!r} or {expected_canonical!r}, observed {actual!r}"
        )


def correction_table_rows(corrections: tuple[TickerEventCorrection, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for correction in corrections:
        evidence_json = json.dumps(
            {
                "url": correction.evidence_url,
                "accession_number": correction.evidence_accession_number,
                "summary": correction.evidence_summary,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        for valid_from_date, ticker in correction.canonical_timeline:
            rows.append(
                {
                    "correction_point_id": _stable_id(
                        "ticker_event_correction_point",
                        f"{correction.correction_id}|{valid_from_date}|{ticker}",
                    ),
                    "correction_id": correction.correction_id,
                    "current_ticker": correction.current_ticker,
                    "composite_figi": correction.composite_figi,
                    "share_class_figi": correction.share_class_figi,
                    "cik": correction.cik,
                    "valid_from_date": valid_from_date,
                    "ticker": ticker,
                    "evidence_url": correction.evidence_url,
                    "evidence_accession_number": correction.evidence_accession_number,
                    "evidence_summary": correction.evidence_summary,
                    "evidence_json": evidence_json,
                    "source_content_sha256": correction.source_content_sha256,
                }
            )
    return rows


def _validate_correction(row: dict[str, Any]) -> TickerEventCorrection:
    required = (
        "correction_id", "current_ticker", "composite_figi", "share_class_figi", "cik",
        "provider_event_signature", "canonical_timeline", "evidence_url", "evidence_accession_number",
        "evidence_summary",
    )
    missing = [key for key in required if not row.get(key)]
    if missing:
        raise ValueError(f"ticker-event correction is missing required fields: {missing}")
    if not str(row["evidence_url"]).startswith("https://www.sec.gov/"):
        raise ValueError("ticker-event correction evidence_url must point to an official SEC source")
    identity = {
        "current_ticker": str(row["current_ticker"]).strip().upper(),
        "composite_figi": str(row["composite_figi"]).strip().upper(),
        "share_class_figi": str(row["share_class_figi"]).strip().upper(),
        "cik": str(row["cik"]).strip(),
    }
    if len(identity["cik"]) != 10 or not identity["cik"].isdigit():
        raise ValueError("ticker-event correction CIK must be a zero-padded 10-digit value")
    provider_signature = _validate_timeline(row["provider_event_signature"], "provider_event_signature")
    canonical_timeline = _validate_timeline(row["canonical_timeline"], "canonical_timeline")
    if canonical_timeline[-1][1] != identity["current_ticker"]:
        raise ValueError("canonical timeline must terminate at current_ticker")
    canonical = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return TickerEventCorrection(
        correction_id=str(row["correction_id"]).strip(),
        **identity,
        provider_event_signature=provider_signature,
        canonical_timeline=canonical_timeline,
        evidence_url=str(row["evidence_url"]).strip(),
        evidence_accession_number=str(row["evidence_accession_number"]).strip(),
        evidence_summary=str(row["evidence_summary"]).strip(),
        source_content_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _validate_timeline(value: Any, field: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a nonempty list")
    points: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field} rows must be objects")
        valid_from = date.fromisoformat(str(item.get("date") or ""))
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError(f"{field} ticker must be nonempty")
        points.append((valid_from.isoformat(), ticker))
    points.sort()
    if len({point[0] for point in points}) != len(points):
        raise ValueError(f"{field} contains duplicate dates")
    return tuple(points)


def _ticker_change_signature(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for raw in events:
        if str(raw.get("type") or "").strip().lower() != "ticker_change":
            continue
        payload = raw.get("ticker_change")
        ticker = str(payload.get("ticker") or "").strip().upper() if isinstance(payload, dict) else ""
        result.append((date.fromisoformat(str(raw.get("date") or "")[:10]).isoformat(), ticker))
    return result


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"

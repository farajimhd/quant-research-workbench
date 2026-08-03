from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .manual_acceptance_review import _manual_unit
from .schema import ANNOTATION_VERSION_V3, stable_json_hash, validate_annotation
from .storage import (
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)


REPAIR_CONTRACT = "news_fresh_acceptance_v2_gold_repairs_v3"
REPAIR_NOTE = "Second fresh-100 exhaustive post-prediction audit correction v3."


@dataclass(frozen=True, slots=True)
class UnitAddition:
    ticker: str
    concepts: tuple[str, ...]
    direction: str
    evidence_contains: str
    positive: int = 0
    negative: int = 0
    issuer_role: str = "mentioned_subject"
    time_orientation: str = "current"


ARTICLE_REPAIRS: dict[str, dict[str, str]] = {
    "N1108": {
        "content_role": "why_moving_followup",
        "source_origin": "editorial_original",
    },
    "N1110": {"source_origin": "editorial_original"},
    "N1116": {"source_origin": "editorial_original"},
    "N1121": {"source_origin": "editorial_original"},
    "N1122": {"source_origin": "editorial_original"},
    "N1125": {"source_origin": "editorial_aggregation"},
    "N1133": {
        "extraction_decision": "labeled",
        "content_role": "market_roundup",
        "source_origin": "editorial_aggregation",
    },
    "N1138": {"source_origin": "issuer_direct"},
    "N1157": {"content_role": "market_roundup"},
    "N1159": {"source_origin": "editorial_original"},
    # A quoted industry executive is commentary, not sell-side research.
    "N1160": {
        "content_role": "editorial_analysis",
        "source_origin": "editorial_original",
    },
    "N1163": {"source_origin": "editorial_original"},
    "N1175": {
        "content_role": "why_moving_followup",
        "source_origin": "editorial_aggregation",
    },
    "N1177": {"source_origin": "editorial_original"},
    "N1173": {"source_origin": "editorial_original"},
    "N1182": {"source_origin": "editorial_original"},
    "N1186": {"source_origin": "editorial_original"},
    "N1183": {"source_origin": "editorial_original"},
}


UNIT_REMOVALS: dict[str, tuple[str, ...]] = {
    # The automated peer table is timestamped May 1, while PNW's cited result
    # is dated May 2.  Future evidence cannot enter a point-in-time gold label.
    "N1105": ("PNW",),
}


UNIT_PATCHES: dict[str, dict[str, dict[str, Any]]] = {
    "N1120": {
        "PGA": {
            "event_concepts": ["financing", "listing_market_structure.ipo"],
        },
    },
    "N1123": {"AMD": {"event_concepts": ["guidance"]}},
    "N1124": {
        "OPGN": {
            "event_concepts": [
                "listing_market_structure.noncompliance",
                "listing_market_structure.reverse_split",
            ],
        },
    },
    "N1176": {
        ticker: {"issuer_role": "primary_subject"}
        for ticker in ("PEP", "PDSB", "MEET", "CBIO", "GPRO", "ANGO", "RECN")
    },
    # Automated summaries are context products, never independent forecast
    # triggers under the frozen acceptance contract.
    "N1166": {
        "FDX": {
            "forecast_trigger_eligible": False,
            "reaction_evaluation_eligible": False,
            "issuer_history_context_eligible": True,
            "eligibility_reason": (
                "Automated summary is contextual evidence, not a new independent causal trigger."
            ),
        },
    },
    "N1160": {
        "RH": {
            "issuer_role": "primary_subject",
            "event_concepts": ["operations", "strategy"],
            "modality": "opinion",
            "analyst_context_eligible": False,
            "analyst_evaluation_eligible": False,
            "analyst_opinions": [],
            "semantic_rationale": (
                "Negative industry-executive commentary concerns RH strategy and sales; "
                "it is not sell-side analyst research."
            ),
        },
    },
    # Neutral counterparties in litigation remain issuer-history context; only
    # the adversely affected defendant is a directional reaction-study unit.
    "N1186": {
        ticker: {
            "forecast_trigger_eligible": False,
            "reaction_evaluation_eligible": False,
            "issuer_history_context_eligible": True,
            "eligibility_reason": (
                "Neutral litigation counterparty context has no directional forecast target."
            ),
        }
        for ticker in ("AZN", "MRK")
    },
}


ADDITIONS: dict[str, tuple[UnitAddition, ...]] = {
    "N1105": (
        UnitAddition(
            "FE", ("earnings",), "negative",
            "FirstEnergy announced earnings on April 25, 2024, revealing results below market expectations.",
            negative=2,
        ),
        UnitAddition(
            "XEL", ("earnings",), "mixed",
            "Xcel Energy's earnings on April 25, 2024 exceeded market projections, revealing an EPS of $0.88 compared to the estimated EPS of $0.78, resulting in a 12.82% increase.",
            positive=2, negative=2,
        ),
    ),
    "N1120": (
        UnitAddition(
            "IMS", ("earnings", "operations"), "mixed",
            "Like PGA, IMS' revenue increased over the past three years, however, the company's bottom line is not as consistent as PGA's.",
            positive=2, negative=2,
        ),
    ),
    "N1133": (
        UnitAddition(
            "ICE", ("ma_transaction",), "neutral",
            "ICE sells Coinbase stake for $1.2B.",
        ),
        UnitAddition(
            "F", ("earnings",), "negative",
            "Market Moving Headline: Ford Motor Co. and EBay Inc. disappointed, while Facebook Inc.'s results took it to a record.",
            negative=2,
        ),
        UnitAddition(
            "EBAY", ("earnings",), "negative",
            "Market Moving Headline: Ford Motor Co. and EBay Inc. disappointed, while Facebook Inc.'s results took it to a record.",
            negative=2,
        ),
        UnitAddition(
            "FB", ("earnings",), "positive",
            "Market Moving Headline: Ford Motor Co. and EBay Inc. disappointed, while Facebook Inc.'s results took it to a record.",
            positive=2,
        ),
        UnitAddition(
            "AAPL", ("earnings", "operations"), "negative",
            "Apple Inc. wiped out earlier earnings-driven gains on concerns about chip shortages.",
            negative=2,
        ),
        UnitAddition(
            "AMZN", ("earnings", "guidance"), "positive",
            "Amazon.com Inc. climbed after hours on a better-than-estimated revenue forecast, while Twitter Inc. sank amid a lackluster outlook.",
            positive=2,
        ),
        UnitAddition(
            "TWTR", ("guidance",), "negative",
            "Amazon.com Inc. climbed after hours on a better-than-estimated revenue forecast, while Twitter Inc. sank amid a lackluster outlook.",
            negative=2,
        ),
    ),
}


def repair_fresh_acceptance_v2_gold(
    root: Path,
    *,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Apply only reviewer-authored gold corrections from the 100-file audit."""
    emit = report or (lambda _message: None)
    annotations = root / "annotations_v3"
    targets = sorted(
        set(ARTICLE_REPAIRS) | set(UNIT_PATCHES) | set(ADDITIONS) | set(UNIT_REMOVALS)
    )
    changes: list[dict[str, Any]] = []
    for index, sample_id in enumerate(targets, 1):
        emit(f"GOLD REPAIR {index}/{len(targets)} {sample_id}")
        path = annotations / f"{sample_id}.json"
        item = read_json(root / "blinded_articles" / f"{sample_id}.json")
        record = read_json(path)
        if REPAIR_NOTE in str(record.get("review_notes") or ""):
            changes.append({"sample_id": sample_id, "status": "already_repaired"})
            continue
        old_hash = str(record.pop("annotation_sha256", ""))
        for key, value in ARTICLE_REPAIRS.get(sample_id, {}).items():
            record[key] = value
        removed = set(UNIT_REMOVALS.get(sample_id, ()))
        if removed:
            record["issuer_units"] = [
                unit for unit in record.get("issuer_units") or ()
                if str(unit.get("ticker") or "").upper() not in removed
            ]
            for disposition in record.get("ticker_dispositions") or ():
                if str(disposition.get("ticker") or "").upper() in removed:
                    disposition.update({
                        "disposition": "incidental_context",
                        "annotation_confidence": 4,
                        "rationale": (
                            "Peer result occurred after the publication timestamp and is "
                            "excluded from point-in-time semantic gold."
                        ),
                        "evidence_quotes": [],
                        "evidence_spans": [],
                        "review_basis": "manual_point_in_time_audit",
                    })
        patched: list[str] = []
        for ticker, updates in UNIT_PATCHES.get(sample_id, {}).items():
            unit = _unit(record, ticker)
            unit.update(updates)
            patched.append(ticker)
        added: list[str] = []
        existing = {str(unit.get("ticker") or "").upper() for unit in record.get("issuer_units") or ()}
        for addition in ADDITIONS.get(sample_id, ()):
            if addition.ticker in existing:
                continue
            quote = _unique_line(item, addition.evidence_contains)
            unit = _manual_unit(
                {
                    "t": addition.ticker,
                    "r": addition.issuer_role,
                    "s": "ticker_specific",
                    "c": list(addition.concepts),
                    "q": [quote],
                    "m": "confirmed",
                    "time": addition.time_orientation,
                    "pos": addition.positive,
                    "neg": addition.negative,
                    "d": addition.direction,
                    "f": False,
                    "e": False,
                    "h": True,
                    "why": "Contextual issuer evidence in an aggregation or comparison; not an independent publication trigger.",
                    "because": "Manual review assigned direction and concepts from the exact cited passage.",
                },
                publication=item.get("publication") or {},
            )
            record.setdefault("issuer_units", []).append(unit)
            _mark_labeled(record, addition.ticker)
            existing.add(addition.ticker)
            added.append(addition.ticker)
        if record.get("content_role") in {"market_roundup", "why_moving_followup"}:
            for unit in record.get("issuer_units") or ():
                unit["forecast_trigger_eligible"] = False
                unit["reaction_evaluation_eligible"] = False
                unit["issuer_history_context_eligible"] = True
                unit["eligibility_reason"] = (
                    "Article is contextual aggregation/follow-up, not a new independent causal trigger."
                )
        record["issuer_units"] = sorted(
            record.get("issuer_units") or (), key=lambda value: str(value.get("ticker") or "")
        )
        record["review_notes"] = " ".join(
            value for value in (str(record.get("review_notes") or "").strip(), REPAIR_NOTE) if value
        )
        record["review_round"] = max(2, int(record.get("review_round") or 1) + 1)
        record["coverage_reviewed_by"] = "codex_primary"
        record["coverage_review_notes"] = (
            "All source lanes, provider metadata, issuer identities and issuer units were re-audited."
        )
        record = materialize_evidence_spans(record, item)
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(f"{sample_id} repaired gold invalid: {validation.errors}")
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append({
            "sample_id": sample_id,
            "status": "repaired",
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": record["annotation_sha256"],
            "patched_tickers": patched,
            "added_tickers": added,
            "removed_tickers": sorted(removed),
        })
    state = refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION_V3)
    manifest = {
        "contract": REPAIR_CONTRACT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "changes": changes,
        "annotation_state_sha256": stable_json_hash(state),
    }
    write_json_atomic(root / "fresh_acceptance_v2_gold_repair_manifest.json", manifest)
    return manifest


def _unit(record: dict[str, Any], ticker: str) -> dict[str, Any]:
    matches = [unit for unit in record.get("issuer_units") or () if str(unit.get("ticker") or "").upper() == ticker]
    if len(matches) != 1:
        raise ValueError(f"expected one gold unit for {ticker}, found {len(matches)}")
    return matches[0]


def _unique_line(item: dict[str, Any], needle: str) -> str:
    text = str((item.get("rendered_product") or {}).get("text") or "")
    matches = [line.strip(" -") for line in text.splitlines() if needle.casefold() in line.casefold()]
    if len(matches) != 1:
        raise ValueError(f"expected one exact evidence line containing {needle!r}, found {len(matches)}")
    return matches[0]


def _mark_labeled(record: dict[str, Any], ticker: str) -> None:
    candidates = {str(value).upper() for value in record.get("candidate_tickers") or ()}
    candidates.add(ticker)
    record["candidate_tickers"] = sorted(candidates)
    dispositions = [
        value for value in record.get("ticker_dispositions") or ()
        if str(value.get("ticker") or "").upper() != ticker
    ]
    dispositions.append({
        "ticker": ticker,
        "disposition": "labeled_issuer_unit",
        "annotation_confidence": 4,
        "rationale": "Exhaustive manual re-review found supported issuer-specific context.",
        "evidence_quotes": [],
        "evidence_spans": [],
        "review_basis": "manual_exhaustive_audit",
    })
    record["ticker_dispositions"] = sorted(dispositions, key=lambda value: str(value.get("ticker") or ""))

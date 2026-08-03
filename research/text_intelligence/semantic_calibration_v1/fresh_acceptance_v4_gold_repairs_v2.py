from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .fresh_acceptance_v2_gold_repairs import _mark_labeled, _unique_line, _unit
from .manual_acceptance_review import _manual_unit
from .schema import ANNOTATION_VERSION_V3, stable_json_hash, validate_annotation
from .storage import (
    assert_runtime_root,
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)


REPAIR_CONTRACT = "news_fresh_acceptance_v4_reviewed_gold_v2"
REPAIR_NOTE = "Fourth fresh-200 second exhaustive manual correction v2."


@dataclass(frozen=True, slots=True)
class Addition:
    ticker: str
    concepts: tuple[str, ...]
    direction: str
    evidence_contains: str
    positive: int = 0
    negative: int = 0
    issuer_role: str = "mentioned_subject"
    forecast: bool = False
    reaction: bool = False
    history: bool = True
    time_orientation: str = "historical"


ARTICLE_PATCHES: dict[str, dict[str, str]] = {
    "N1327": {"source_origin": "editorial_original"},
    "N1328": {"content_role": "primary_event", "source_origin": "editorial_aggregation"},
    "N1352": {"source_origin": "editorial_original"},
    "N1354": {"source_origin": "editorial_original"},
    "N1358": {"source_origin": "editorial_original"},
    "N1361": {"source_origin": "editorial_original"},
    "N1362": {"source_origin": "editorial_original"},
    "N1363": {"source_origin": "editorial_original"},
    "N1364": {"source_origin": "editorial_original"},
    "N1366": {"content_role": "mover_recap", "source_origin": "automated_summary"},
    "N1372": {"source_origin": "editorial_original"},
    "N1375": {"source_origin": "editorial_original"},
    "N1378": {"source_origin": "editorial_original"},
    "N1384": {"source_origin": "editorial_original"},
    "N1461": {"source_origin": "editorial_aggregation"},
    "N1469": {"source_origin": "editorial_aggregation"},
    "N1471": {"source_origin": "editorial_aggregation"},
    "N1496": {"content_role": "regulatory_event"},
    "N1498": {"content_role": "analyst_event", "source_origin": "analyst_research"},
}

UNIT_REMOVALS: dict[str, tuple[str, ...]] = {
    "N1358": ("F", "HMC", "TM"),
    "N1498": ("ODP", "SPLS"),
}

UNIT_PATCHES: dict[str, dict[str, dict[str, Any]]] = {
    "N1319": {"DPLO": {"issuer_role": "acquirer"}},
    "N1328": {"WERN": {
        "forecast_trigger_eligible": True,
        "reaction_evaluation_eligible": True,
        "issuer_history_context_eligible": True,
        "eligibility_reason": "Current issuer event is eligible for forecast and reaction evaluation.",
    }},
    "N1338": {
        "CBS": {"issuer_role": "counterparty"},
        "VIAB": {"issuer_role": "counterparty"},
    },
    "N1354": {"VRNT": {
        "issuer_role": "primary_subject",
        "forecast_trigger_eligible": True,
        "reaction_evaluation_eligible": True,
        "issuer_history_context_eligible": True,
        "eligibility_reason": "Current issuer financing event is eligible for forecast and reaction evaluation.",
    }},
    "N1357": {"ABBV": {
        "semantic_direction": "neutral",
        "positive_evidence_level": 1,
        "negative_evidence_level": 1,
        "semantic_rationale": "The filing reports financing terms without supported directional issuer language.",
    }},
}

CONCEPT_ADDS: dict[str, dict[str, tuple[str, ...]]] = {
    "N1307": {"NPSP": ("product_commercial", "regulatory")},
    "N1322": {"FISI": ("financing", "operations")},
    "N1323": {"UNH": ("options_activity",)},
    "N1331": {ticker: ("earnings",) for ticker in ("DUK", "POM", "T")},
    "N1339": {"TSLA": ("ma_transaction",)},
    "N1343": {ticker: ("ownership", "financing") for ticker in ("AIG", "TRH")},
    "N1346": {"CNK": ("ownership", "financing")},
    "N1347": {"DOV": ("guidance",)},
    "N1353": {"PHRRF": ("contract_order",), "FTHWF": ("ma_transaction", "operations")},
    "N1365": {"DSCI": ("guidance",)},
    "N1368": {"CSCO": ("guidance", "management_governance")},
    "N1371": {"ADBE": ("guidance",)},
    "N1372": {"JCP": ("guidance", "operations")},
    "N1376": {"TNYA": ("clinical",)},
    "N1379": {"MODN": ("guidance",)},
    "N1380": {ticker: ("guidance",) for ticker in ("LOW", "CAR")},
    "N1384": {"NVAX": ("listing_market_structure",)},
    "N1386": {"AMZN": ("options_activity",)},
    "N1390": {"TSLA": ("guidance",)},
    "N1391": {"AGN": ("guidance",)},
    "N1393": {"UPST": ("guidance",), "PINS": ("guidance",), "RIVN": ("product_commercial",)},
    "N1395": {"WHLRP": ("listing_market_structure",)},
    "N1399": {"BF.B": ("ma_transaction",)},
    "N1421": {"CHGG": ("guidance",)},
    "N1429": {"JASO": ("guidance",)},
    "N1434": {"LM": ("ma_transaction",)},
    "N1437": {ticker: ("guidance",) for ticker in ("NMBL", "MENT")},
    "N1452": {"RCL": ("guidance",)},
    "N1459": {"RAD": ("guidance",)},
    "N1464": {"TWOU": ("analyst_action",)},
    "N1468": {"CALI": ("ma_transaction",)},
    "N1474": {"TLVT": ("guidance", "contract_order")},
    "N1488": {
        "UUP": ("market_reaction",),
        "BUCY": ("market_reaction",),
        "KMP": ("market_reaction",),
        "ROVI": ("earnings",),
    },
    "N1494": {"BBY": ("strategy_valuation",)},
    "N1498": {"OMX": ("guidance", "legal")},
}

CONCEPT_REPLACEMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "N1378": {"CMCL": ("guidance", "operations")},
    "N1397": {"BOOT": ("guidance",)},
    "N1415": {"USAT": ("guidance", "financing")},
    "N1416": {"HLIT": ("guidance",)},
    "N1424": {"CC": ("guidance",)},
    "N1444": {"RIVN": ("guidance", "operations")},
    "N1475": {"EXPE": ("earnings",), "TRIP": ("earnings",)},
    "N1488": {"SLW": (), "UUP": ("market_reaction",)},
}

ADDITIONS: dict[str, tuple[Addition, ...]] = {
    "N1305": (Addition("DRAD", ("ma_transaction",), "neutral", "Digirad", issuer_role="acquirer"),),
    "N1307": (Addition("AMGN", ("earnings", "product_commercial"), "neutral", "Royalty revenues climbed"),),
    "N1448": (Addition("VST", ("options_activity",), "negative", "Financial giants", negative=3),),
    "N1462": (Addition(
        "MIME", ("legal",), "positive", "Finjan And Mimecast", positive=2,
        issuer_role="counterparty", forecast=True, reaction=True, time_orientation="current",
    ),),
    "N1484": (
        Addition("BKS", ("earnings",), "neutral", "Barnes and Noble"),
        Addition("KR", ("earnings",), "neutral", "Kroger"),
        Addition("TIF", ("earnings", "market_reaction"), "neutral", "shares rose 0.82"),
        Addition("ZUMZ", ("earnings",), "neutral", "Zumiez"),
    ),
}


def build_reviewed_gold_v2(
    source_root: Path,
    target_root: Path,
    *,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Apply the second exhaustive review non-destructively to reviewed V1 gold."""
    assert_runtime_root(source_root)
    assert_runtime_root(target_root)
    if target_root.exists():
        raise FileExistsError(f"reviewed gold target already exists: {target_root}")
    emit = report or (lambda _message: None)
    shutil.copytree(source_root, target_root)

    touched = sorted(
        set(ARTICLE_PATCHES) | set(UNIT_REMOVALS) | set(UNIT_PATCHES)
        | set(CONCEPT_ADDS) | set(CONCEPT_REPLACEMENTS) | set(ADDITIONS)
    )
    changes: list[dict[str, Any]] = []
    for index, sample_id in enumerate(touched, 1):
        emit(f"REVIEWED GOLD V2 {index}/{len(touched)} {sample_id}")
        item = read_json(target_root / "blinded_articles" / f"{sample_id}.json")
        path = target_root / "annotations_v3" / f"{sample_id}.json"
        record = read_json(path)
        old_hash = str(record.pop("annotation_sha256", ""))

        record.update(ARTICLE_PATCHES.get(sample_id, {}))
        removals = set(UNIT_REMOVALS.get(sample_id, ()))
        if removals:
            record["issuer_units"] = [
                unit for unit in record.get("issuer_units") or ()
                if str(unit.get("ticker") or "").upper() not in removals
            ]
            for ticker in removals:
                _mark_excluded(record, ticker)
        for ticker, updates in UNIT_PATCHES.get(sample_id, {}).items():
            _unit(record, ticker).update(updates)
        for ticker, concepts in CONCEPT_REPLACEMENTS.get(sample_id, {}).items():
            _unit(record, ticker)["event_concepts"] = list(concepts)
        for ticker, concepts in CONCEPT_ADDS.get(sample_id, {}).items():
            unit = _unit(record, ticker)
            unit["event_concepts"] = sorted(set(unit.get("event_concepts") or ()) | set(concepts))

        existing = {str(unit.get("ticker") or "").upper() for unit in record.get("issuer_units") or ()}
        added: list[str] = []
        for addition in ADDITIONS.get(sample_id, ()):
            if addition.ticker in existing:
                raise ValueError(f"{sample_id} addition already exists: {addition.ticker}")
            quote = _unique_line(item, addition.evidence_contains)
            record.setdefault("issuer_units", []).append(_manual_unit({
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
                "f": addition.forecast,
                "e": addition.reaction,
                "h": addition.history,
                "why": (
                    "Current issuer event is eligible for forecast and reaction evaluation."
                    if addition.forecast
                    else "Contextual evidence is retained for issuer history only."
                ),
                "because": "Second exhaustive manual review assigned the cited issuer-scoped evidence.",
            }, publication=item.get("publication") or {}))
            _mark_labeled(record, addition.ticker)
            existing.add(addition.ticker)
            added.append(addition.ticker)

        record["extraction_decision"] = "labeled" if record.get("issuer_units") else record["extraction_decision"]
        record["issuer_units"] = sorted(record.get("issuer_units") or (), key=lambda value: str(value.get("ticker") or ""))
        record["review_notes"] = " ".join(filter(None, (str(record.get("review_notes") or "").strip(), REPAIR_NOTE)))
        record["review_round"] = int(record.get("review_round") or 1) + 1
        record["coverage_reviewed_by"] = "codex_primary"
        record["coverage_review_notes"] = "Every original source lane and every gold/V9 field was manually re-audited."
        record = materialize_evidence_spans(record, item)
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(f"{sample_id} reviewed gold V2 invalid: {validation.errors}")
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append({
            "sample_id": sample_id,
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": record["annotation_sha256"],
            "added_tickers": added,
            "removed_tickers": sorted(removals),
        })

    state = refresh_annotation_state(target_root, annotation_version=ANNOTATION_VERSION_V3)
    manifest = {
        "contract": REPAIR_CONTRACT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "changes": changes,
        "annotation_state_sha256": stable_json_hash(state),
    }
    write_json_atomic(target_root / "reviewed_gold_v2_manifest.json", manifest)
    return manifest


def _mark_excluded(record: dict[str, Any], ticker: str) -> None:
    dispositions = [
        value for value in record.get("ticker_dispositions") or ()
        if str(value.get("ticker") or "").upper() != ticker
    ]
    dispositions.append({
        "ticker": ticker,
        "disposition": "incidental_context",
        "annotation_confidence": 4,
        "rationale": "Second exhaustive review found no issuer-specific event or substantive thesis.",
        "evidence_quotes": [],
        "evidence_spans": [],
        "review_basis": "manual_exhaustive_audit",
    })
    record["ticker_dispositions"] = sorted(dispositions, key=lambda value: str(value.get("ticker") or ""))

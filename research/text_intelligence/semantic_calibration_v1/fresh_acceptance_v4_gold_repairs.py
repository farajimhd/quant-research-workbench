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


REPAIR_CONTRACT = "news_fresh_acceptance_v4_reviewed_gold_v1"
REPAIR_NOTE = "Fourth fresh-200 exhaustive post-baseline manual correction v1."


@dataclass(frozen=True, slots=True)
class Addition:
    ticker: str
    concepts: tuple[str, ...]
    direction: str
    evidence_contains: str
    positive: int = 0
    negative: int = 0


ARTICLE_REPAIRS: dict[str, dict[str, str]] = {
    "N1318": {"content_role": "editorial_analysis", "source_origin": "editorial_original"},
    "N1327": {"source_origin": "editorial_aggregation"},
    "N1336": {"source_origin": "editorial_original"},
    "N1340": {"source_origin": "editorial_aggregation"},
    "N1357": {"source_origin": "editorial_aggregation"},
    "N1361": {"source_origin": "editorial_aggregation"},
    "N1370": {"content_role": "why_moving_followup", "source_origin": "editorial_aggregation"},
    "N1376": {"source_origin": "editorial_aggregation"},
    "N1391": {"source_origin": "editorial_aggregation"},
    "N1392": {"source_origin": "issuer_direct"},
    "N1395": {"source_origin": "editorial_aggregation"},
    "N1396": {"source_origin": "editorial_aggregation"},
    "N1399": {"source_origin": "editorial_aggregation"},
    "N1403": {"content_role": "mover_recap", "source_origin": "editorial_aggregation"},
    "N1426": {"source_origin": "editorial_aggregation"},
    "N1427": {"content_role": "mover_recap", "source_origin": "editorial_aggregation"},
    "N1436": {"content_role": "mover_recap", "source_origin": "editorial_aggregation"},
    "N1444": {"content_role": "editorial_analysis", "source_origin": "editorial_original"},
    "N1445": {"source_origin": "editorial_original"},
    "N1472": {"source_origin": "editorial_aggregation"},
    "N1492": {"source_origin": "editorial_aggregation"},
    "N1495": {"source_origin": "editorial_aggregation"},
    "N1496": {"source_origin": "editorial_aggregation"},
}

UNIT_PATCHES: dict[str, dict[str, dict[str, Any]]] = {
    "N1329": {"AIG": {"semantic_direction": "mixed", "negative_evidence_level": 2}},
    "N1338": {"VIAB": {"semantic_direction": "neutral", "positive_evidence_level": 0}},
    "N1351": {"LGIH": {"semantic_direction": "mixed", "positive_evidence_level": 2}},
    "N1391": {"VRX": {"semantic_direction": "mixed", "positive_evidence_level": 2, "negative_evidence_level": 2}},
}

CONTEXT_ONLY_ARTICLES = frozenset({"N1370", "N1479", "N1498"})

ADDITIONS: dict[str, tuple[Addition, ...]] = {
    "N1305": (
        Addition("CDTI", ("product_commercial", "management_governance"), "positive", "Clean Diesel Technologies", 2, 0),
        Addition("LAKE", ("commercial", "financing"), "mixed", "Lakeland Industries", 2, 2),
        Addition("PDII", ("ma_transaction", "management_governance"), "mixed", "PDI (NASDAQ: PDII)", 2, 2),
        Addition("OCLS", ("regulatory", "product_commercial"), "positive", "Oculus Innovative Sciences", 2, 0),
        Addition("ALU", ("earnings",), "positive", "Alcatel-Lucent", 2, 0),
        Addition("LOPE", ("earnings", "strategic_alternatives"), "positive", "Grand Canyon Education", 2, 0),
        Addition("ACHC", ("earnings", "guidance", "ma_transaction"), "positive", "Acadia Healthcare Company", 3, 0),
        Addition("V", ("earnings", "capital_return"), "positive", "Visa (NYSE: V)", 3, 0),
        Addition("MA", ("earnings",), "positive", "MasterCard", 2, 0),
        Addition("NVAX", ("regulatory", "clinical"), "positive", "Novavax", 2, 0),
        Addition("APD", ("earnings",), "positive", "Air Products & Chemicals", 2, 0),
    ),
    "N1311": (
        Addition("ULTA", ("earnings", "guidance"), "mixed", "ULTA Salon", 2, 2),
        Addition("TRVN", ("financing",), "negative", "Trevena", 0, 2),
        Addition("GPS", ("earnings",), "positive", "The Gap", 2, 0),
    ),
    "N1323": (Addition("UNH", ("options_activity", "analyst_action"), "mixed", "overall sentiment", 2, 2),),
    "N1354": (Addition("VRNT", ("financing", "strategic_investment"), "mixed", "Verint Sees Closing", 2, 2),),
    "N1358": (
        Addition("F", ("competitive_context",), "neutral", "Ford Motor Company", 0, 0),
        Addition("HMC", ("competitive_context",), "neutral", "Honda Motor Co", 0, 0),
        Addition("TM", ("competitive_context",), "neutral", "Toyota Motor Corp", 0, 0),
    ),
    "N1366": tuple(
        Addition(ticker, ("earnings",), "neutral", phrase)
        for ticker, phrase in (
            ("MRVL", "Marvell Technology"), ("YEXT", "Yext"),
            ("GWRE", "Guidewire Software"), ("SMAR", "Smartsheet"),
            ("DOCU", "DocuSign"), ("ASAN", "Asana"), ("DOMO", "Domo"),
        )
    ),
    "N1386": (Addition("AMZN", ("options_activity", "analyst_action"), "positive", "61% of the investors", 2, 1),),
    "N1405": (Addition("ENOC", ("editorial_rebound_thesis",), "positive", "EnerNOC", 2, 0),),
}


def build_reviewed_gold(
    source_root: Path,
    target_root: Path,
    *,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create a non-destructive reviewed authority from the frozen baseline."""
    assert_runtime_root(source_root)
    assert_runtime_root(target_root)
    if target_root.exists():
        raise FileExistsError(f"reviewed gold target already exists: {target_root}")
    emit = report or (lambda _message: None)
    target_root.mkdir(parents=True)
    shutil.copy2(source_root / "sample_manifest.json", target_root / "sample_manifest.json")
    shutil.copytree(source_root / "blinded_articles", target_root / "blinded_articles")
    shutil.copytree(source_root / "sealed", target_root / "sealed")
    shutil.copytree(source_root / "annotations_v3", target_root / "annotations_v3")

    targets = sorted(set(ARTICLE_REPAIRS) | set(UNIT_PATCHES) | set(ADDITIONS) | set(CONTEXT_ONLY_ARTICLES))
    changes: list[dict[str, Any]] = []
    for index, sample_id in enumerate(targets, 1):
        emit(f"REVIEWED GOLD {index}/{len(targets)} {sample_id}")
        item = read_json(target_root / "blinded_articles" / f"{sample_id}.json")
        path = target_root / "annotations_v3" / f"{sample_id}.json"
        record = read_json(path)
        old_hash = str(record.pop("annotation_sha256", ""))
        for key, value in ARTICLE_REPAIRS.get(sample_id, {}).items():
            record[key] = value
        for ticker, updates in UNIT_PATCHES.get(sample_id, {}).items():
            _unit(record, ticker).update(updates)
        existing = {str(unit.get("ticker") or "").upper() for unit in record.get("issuer_units") or ()}
        added: list[str] = []
        for addition in ADDITIONS.get(sample_id, ()):
            if addition.ticker in existing:
                continue
            quote = _unique_line(item, addition.evidence_contains)
            record.setdefault("issuer_units", []).append(_manual_unit({
                "t": addition.ticker,
                "r": "mentioned_subject",
                "s": "ticker_specific",
                "c": list(addition.concepts),
                "q": [quote],
                "m": "confirmed",
                "time": "historical",
                "pos": addition.positive,
                "neg": addition.negative,
                "d": addition.direction,
                "f": False,
                "e": False,
                "h": True,
                "why": "Contextual evidence in an aggregation or analysis; not an independent trigger.",
                "because": "Exhaustive manual review assigned the cited issuer-scoped evidence.",
            }, publication=item.get("publication") or {}))
            _mark_labeled(record, addition.ticker)
            existing.add(addition.ticker)
            added.append(addition.ticker)
        if added and record["extraction_decision"] != "labeled":
            record["extraction_decision"] = "labeled"
        if sample_id in CONTEXT_ONLY_ARTICLES:
            for unit in record.get("issuer_units") or ():
                unit.update({
                    "forecast_trigger_eligible": False,
                    "reaction_evaluation_eligible": False,
                    "issuer_history_context_eligible": True,
                    "eligibility_reason": "Editorial or follow-up context is not an independent causal trigger.",
                })
        record["issuer_units"] = sorted(record.get("issuer_units") or (), key=lambda value: str(value.get("ticker") or ""))
        record["review_notes"] = " ".join(filter(None, (str(record.get("review_notes") or "").strip(), REPAIR_NOTE)))
        record["review_round"] = max(2, int(record.get("review_round") or 1) + 1)
        record["coverage_reviewed_by"] = "codex_primary"
        record["coverage_review_notes"] = "All original metadata, source lanes and issuer evidence were exhaustively re-audited."
        record = materialize_evidence_spans(record, item)
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(f"{sample_id} reviewed gold invalid: {validation.errors}")
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append({
            "sample_id": sample_id,
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": record["annotation_sha256"],
            "added_tickers": added,
        })
    state = refresh_annotation_state(target_root, annotation_version=ANNOTATION_VERSION_V3)
    manifest = {
        "contract": REPAIR_CONTRACT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "changes": changes,
        "annotation_state_sha256": stable_json_hash(state),
    }
    write_json_atomic(target_root / "reviewed_gold_manifest.json", manifest)
    return manifest

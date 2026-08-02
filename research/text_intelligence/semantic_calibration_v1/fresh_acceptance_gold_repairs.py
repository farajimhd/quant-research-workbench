from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .manual_acceptance_review import _manual_unit
from .schema import ANNOTATION_VERSION_V3, stable_json_hash, validate_annotation
from .storage import (
    annotation_directory,
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)


REPAIR_CONTRACT = "news_fresh_acceptance_gold_repairs_v1"


@dataclass(frozen=True, slots=True)
class UnitRepair:
    ticker: str
    concept: str
    direction: str
    evidence_contains: str
    issuer_role: str = "primary_subject"
    positive: int = 0
    negative: int = 0
    modality: str = "confirmed"
    time_orientation: str = "current"


# These are human-reference corrections, not V9 rules.  Every added unit is
# tied to a reviewer-inspected, exact source sentence and remains contextual
# because roundup/follow-up articles are not independent causal triggers.
ADDITIONS: dict[str, tuple[UnitRepair, ...]] = {
    "N1048": (
        UnitRepair("VTTI", "ma_transaction.ownership_transfer", "neutral", "has agreed to acquire MISC Berhad's 50% shareholding"),
        UnitRepair("THS", "ma_transaction.acquisition_discussion", "neutral", "is in talks to buy ConAgra's private-label Ralcorp", modality="rumored"),
        UnitRepair("POST", "ma_transaction.potential_bidder", "neutral", "Other firms looking at Ralcorp", modality="rumored"),
    ),
    "N1057": (
        UnitRepair("VRX", "earnings.strong", "positive", "soared more than 25 percent", positive=2),
        UnitRepair("ENDP", "earnings.strong", "positive", "surged 22 percent after a strong earnings report", positive=2),
        UnitRepair("IPXL", "earnings.miss", "negative", "lost 23 percent after an earnings miss", negative=2),
        UnitRepair("MXL", "earnings.miss", "negative", "fell 21 percent after Q2 earnings", negative=1),
    ),
    "N1059": (
        UnitRepair("HPCO", "financing.public_offering", "mixed", "planned to offer 1 million shares", positive=2, negative=2),
        UnitRepair("CMRA", "financing.equity_purchase_agreement", "negative", "purchase agreement with Arena Business Solutions", negative=1),
        UnitRepair("SHPH", "listing_market_structure.ipo", "neutral", "priced its IPO at $8.125 per unit"),
        UnitRepair("VXRT", "clinical.positive_data", "positive", "demonstrating safety and immunogenicity", positive=2),
        UnitRepair("NTNX", "earnings.beat", "positive", "better-than-expected sales results", positive=2),
        UnitRepair("TRQ", "ma_transaction.acquisition_target", "positive", "acquire full ownership of Turquoise Hill", positive=2),
        UnitRepair("PSTG", "earnings.beat", "positive", "Pure Storage, Inc.", positive=2),
        UnitRepair("MDB", "guidance.below_estimates", "negative", "issued earnings guidance below analyst estimates", negative=2),
        UnitRepair("NUWE", "clinical.positive_data", "positive", "new clinical data demonstrating 100% survival", positive=2, time_orientation="historical"),
        UnitRepair("SMTC", "guidance.below_estimates", "negative", "Semtech Corporation", negative=2),
        UnitRepair("OKTA", "guidance.below_estimates", "negative", "Canaccord Genuity downgraded Okta", negative=2),
        UnitRepair("VEEV", "guidance.below_estimates", "negative", "Veeva Systems Inc.", negative=2),
        UnitRepair("NVDA", "regulatory.export_restriction", "negative", "secure a license for exporting its powerful AI processors", negative=2),
        UnitRepair("HRL", "earnings.guidance_cut", "negative", "posted downbeat Q3 earnings and lowered", negative=2),
        UnitRepair("AMD", "regulatory.export_restriction", "negative", "Advanced Micro Devices", negative=2),
    ),
    "N1060": (
        UnitRepair("MRK", "clinical.positive_data", "positive", "Merck & Co.", positive=1),
        UnitRepair("EPZM", "analyst.rating_upgrade", "positive", "Morgan Stanley upgraded shares", positive=1),
        UnitRepair("NVS", "clinical.endpoint_met", "positive", "thus meeting the primary endpoint", positive=2),
    ),
    "N1061": (
        UnitRepair("BID", "earnings.miss", "negative", "weaker-than-expected second-quarter earnings", negative=2),
        UnitRepair("ZNGA", "guidance.lowered", "negative", "lowered its 2014 earnings forecast", negative=2),
        UnitRepair("MELI", "earnings.beat", "positive", "stronger-than-expected quarterly results", positive=2),
        UnitRepair("NVDA", "earnings.guidance_raise", "positive", "issued a strong revenue forecast", positive=2),
        UnitRepair("POST", "earnings.loss", "negative", "reported a Q3 loss", negative=2),
        UnitRepair("VOLC", "earnings.results", "negative", "Volcano (NASDAQ: VOLC) shares tumbled", negative=1),
        UnitRepair("STJ", "legal.settlement", "neutral", "settlement agreement with St. Jude Medical"),
    ),
    "N1069": (
        UnitRepair("OPK", "regulatory.sec_settlement", "negative", "agreement with the SEC to resolve", negative=1),
        UnitRepair("QTM", "financing.debt_refinancing", "positive", "secured $210 million in long-term financing", positive=1),
        UnitRepair("AWSM", "listing_market_structure.compliance", "positive", "intention to regain Nasdaq listing compliance", positive=1),
        UnitRepair("VTVT", "financing.equity_purchase", "negative", "purchase 815k shares", negative=1),
        UnitRepair("OBLN", "financing.dilutive", "negative", "two financing agreements", negative=2),
        UnitRepair("RSLS", "listing_market_structure.delisting", "negative", "shares will be delisted", negative=2),
    ),
    "N1073": (
        UnitRepair("PFE", "regulatory.fda_panel_support", "positive", "green light on their COVID-19 vaccine", positive=2),
        UnitRepair("BNTX", "regulatory.fda_panel_support", "positive", "green light on their COVID-19 vaccine", positive=2),
        UnitRepair("TPGY", "ma_transaction.merger", "positive", "merger deal with EV charging company", positive=2),
        UnitRepair("VTVT", "financing.equity_purchase", "positive", "purchased 625,000 shares", positive=1),
        UnitRepair("SNOA", "commercial.licensing_agreement", "positive", "agreement with Crown Laboratories", positive=1),
        UnitRepair("SLS", "clinical.followup_data", "neutral", "follow-up data from a randomized Phase 2"),
        UnitRepair("IMMP", "clinical.trial_start", "positive", "will commence a new Phase 2 clinical trial", positive=1, time_orientation="historical"),
        UnitRepair("NBRV", "financing.public_offering", "negative", "pricing of $15 million public offering", negative=2),
    ),
    "N1074": (
        UnitRepair("EARS", "listing_market_structure.compliance", "positive", "regained compliance with Nasdaq", positive=1),
        UnitRepair("ONCS", "listing_market_structure.reverse_split", "negative", "1-for-10 reverse stock split", negative=1),
        UnitRepair("TBPH", "clinical.positive_data", "positive", "additional Phase 3 data", positive=1),
        UnitRepair("TNXP", "management_governance.resignation", "neutral", "resignation of Donald Landry"),
        UnitRepair("CLVS", "clinical.positive_data", "positive", "new data from the Phase 3 ARIEL3", positive=1),
        UnitRepair("GH", "financing.public_offering", "negative", "underwritten public offering of 4.5 million shares", negative=2),
        UnitRepair("AKRX", "regulatory.fda_approval", "positive", "FDA has approved its generic version", positive=2),
        UnitRepair("INSM", "clinical.positive_data", "positive", "sustained culture conversion", positive=2),
        UnitRepair("VAR", "ma_transaction.acquisition", "mixed", "agreement to acquire privately-held Cancer Treatment", positive=2, negative=2),
    ),
    "N1075": (
        UnitRepair("DRYS", "listing_market_structure.reverse_split", "negative", "1-for-4 reverse stock split", negative=1),
        UnitRepair("NPTN", "management_governance.resignation", "neutral", "resignation of its CFO"),
    ),
    "N1076": (
        UnitRepair("MS", "earnings.beat", "positive", "Wall Street analysts were expecting the company to earn $1.25", positive=2),
        UnitRepair("ISRG", "earnings.beat", "positive", "Wall Street analysts were expecting the company to earn $2.09", positive=2),
    ),
    "N1086": (
        UnitRepair("YCBD", "management_governance.appointment", "neutral", "hiring Dave Johnson as senior vice president"),
    ),
    "N1096": (
        UnitRepair("GAME", "earnings.guidance_reiterated", "positive", "first positive adjusted EBITDA", positive=2),
        UnitRepair("COST", "earnings.sales_growth", "positive", "increase of 11.3%", positive=1),
        UnitRepair("DLR", "operations.capex_expansion", "positive", "targeting nearly S$7 billion", positive=1),
        UnitRepair("STZ", "guidance.below_estimates", "negative", "versus the $12.37 analyst estimate", negative=2),
        UnitRepair("ARAI", "legal.patent_grant", "positive", "secured its tenth U.S. patent", positive=1),
    ),
}


FLAG_REPAIRS: dict[str, dict[str, tuple[bool, bool, bool]]] = {
    "N1022": {"JYNT": (False, False, True)},
    "N1049": {"MCD": (False, False, True)},
    "N1092": {"MTCH": (False, False, True)},
}


def repair_fresh_acceptance_gold(
    root: Path, *, report: Callable[[str], None] | None = None
) -> dict[str, Any]:
    emit = report or (lambda _message: None)
    annotations = annotation_directory(root, ANNOTATION_VERSION_V3)
    targets = sorted(set(ADDITIONS) | set(FLAG_REPAIRS) | {"N1035"})
    changes: list[dict[str, Any]] = []
    for index, sample_id in enumerate(targets, start=1):
        emit(f"REPAIR {index}/{len(targets)} {sample_id}")
        path = annotations / f"{sample_id}.json"
        item = read_json(root / "blinded_articles" / f"{sample_id}.json")
        record = read_json(path)
        if "Fresh-100 exhaustive coverage and eligibility correction" in str(
            record.get("review_notes") or ""
        ):
            emit(f"ALREADY REPAIRED {sample_id}")
            changes.append({
                "sample_id": sample_id,
                "status": "already_repaired",
                "old_annotation_sha256": str(record.get("annotation_sha256") or ""),
                "new_annotation_sha256": str(record.get("annotation_sha256") or ""),
                "reviewed_issuer_units": [
                    value.ticker for value in ADDITIONS.get(sample_id, ())
                ],
                "eligibility_updates": sorted(FLAG_REPAIRS.get(sample_id, {})),
            })
            continue
        old_hash = str(record.pop("annotation_sha256", ""))
        old_units = {str(unit["ticker"]).upper() for unit in record.get("issuer_units") or ()}
        added: list[str] = []
        for repair in ADDITIONS.get(sample_id, ()):
            if repair.ticker in old_units:
                continue
            quote = _line_containing(item, repair.evidence_contains)
            unit = _manual_unit(
                {
                    "t": repair.ticker,
                    "r": repair.issuer_role,
                    "s": "ticker_specific",
                    "c": [repair.concept],
                    "q": [quote],
                    "m": repair.modality,
                    "time": repair.time_orientation,
                    "pos": repair.positive,
                    "neg": repair.negative,
                    "d": repair.direction,
                    "f": False,
                    "e": False,
                    "h": True,
                    "why": "Issuer-specific context inside an aggregation; not an independent causal trigger.",
                    "because": "Manual review assigned the event and direction from the cited issuer-specific passage.",
                },
                publication=item.get("publication") or {},
            )
            record.setdefault("issuer_units", []).append(unit)
            _mark_labeled(record, repair.ticker)
            added.append(repair.ticker)
        flag_changes: list[str] = []
        for ticker, flags in FLAG_REPAIRS.get(sample_id, {}).items():
            unit = _unit(record, ticker)
            unit["forecast_trigger_eligible"], unit["reaction_evaluation_eligible"], unit["issuer_history_context_eligible"] = flags
            unit["eligibility_reason"] = "Point-in-time review: contextual issuer evidence without a tradable independent trigger."
            flag_changes.append(ticker)
        record["extraction_decision"] = "labeled" if record.get("issuer_units") else "no_supported_event"
        record["review_round"] = int(record.get("review_round") or 1) + 1
        record["reviewer"] = "codex_primary"
        note = "Fresh-100 exhaustive coverage and eligibility correction after source-text audit."
        if sample_id == "N1035":
            note += " Verified that the article contains price observations and stale rating context, but no current supported issuer event."
        record["review_notes"] = f"{str(record.get('review_notes') or '').strip()} {note}".strip()
        record["coverage_review_notes"] = "Every issuer-specific passage was re-reviewed; price-only and stale-context candidates remain dispositions."
        record = materialize_evidence_spans(record, item)
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(f"invalid repaired annotation {sample_id}: {', '.join(validation.errors)}")
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append({
            "sample_id": sample_id,
            "status": "repaired",
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": record["annotation_sha256"],
            "reviewed_issuer_units": added,
            "eligibility_updates": flag_changes,
        })
        emit(f"REPAIRED {sample_id} units_added={len(added)} flags={len(flag_changes)}")
    refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION_V3)
    manifest = {
        "contract": REPAIR_CONTRACT,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "certified_records": len(changes),
        "changed_this_run": sum(value["status"] == "repaired" for value in changes),
        "changes": changes,
    }
    output = root / "corrections" / f"{REPAIR_CONTRACT}.json"
    write_json_atomic(output, manifest)
    return manifest


def _line_containing(item: dict[str, Any], needle: str) -> str:
    text = str((item.get("rendered_product") or {}).get("text") or "")
    matches = [line.strip() for line in text.splitlines() if needle.casefold() in line.casefold()]
    if len(matches) != 1:
        raise ValueError(f"expected one evidence line for {needle!r}, found {len(matches)}")
    return matches[0]


def _unit(record: dict[str, Any], ticker: str) -> dict[str, Any]:
    matches = [unit for unit in record.get("issuer_units") or () if str(unit.get("ticker") or "").upper() == ticker]
    if len(matches) != 1:
        raise ValueError(f"expected one {ticker} issuer unit, found {len(matches)}")
    return matches[0]


def _mark_labeled(record: dict[str, Any], ticker: str) -> None:
    ticker = ticker.upper()
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
        "rationale": "Manual re-review found a supported issuer-specific semantic unit.",
        "evidence_quotes": [],
        "evidence_spans": [],
        "review_basis": "manual_review",
    })
    record["ticker_dispositions"] = sorted(dispositions, key=lambda value: str(value["ticker"]))

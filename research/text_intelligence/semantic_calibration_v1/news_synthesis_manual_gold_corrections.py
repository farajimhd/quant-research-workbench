from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.text_intelligence.semantic_calibration_v1.schema import (
    ANNOTATION_VERSION_V3,
    stable_json_hash,
    validate_annotation,
)
from research.text_intelligence.semantic_calibration_v1.storage import (
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)

from research.text_intelligence.news_synthesis_v1.certification import (
    certify_documents,
    default_certification_config,
    render_review_packet,
)
from research.text_intelligence.news_synthesis_v1.review_spec import compile_review_spec
from research.text_intelligence.news_synthesis_v1.taxonomy_audit import discover_pairs


CORRECTION_VERSION = "news_synthesis_manual_gold_corrections_v3"


@dataclass(frozen=True, slots=True)
class GoldCorrection:
    sample_id: str
    ticker: str
    direction: str
    positive_strength: int
    negative_strength: int
    rationale: str
    review_policy: str = "evidence_balance"


@dataclass(frozen=True, slots=True)
class HistoricalRecapCorrection:
    sample_id: str
    ticker: str
    content_role: str
    communication_purpose: str
    eligibility_reason: str


@dataclass(frozen=True, slots=True)
class ReplacementEvidence:
    concept_leaf: str
    evidence: str
    semantic_sentiment: str
    sentiment_strength: int
    statement_kind: str = "event"
    epistemic_status: str = "confirmed"
    time_relation: str = "current"


@dataclass(frozen=True, slots=True)
class ReviewedSpecCorrection:
    sample_id: str
    ticker: str
    direction: str | None
    positive_strength: int
    negative_strength: int
    rationale: str
    replacement_evidence: tuple[ReplacementEvidence, ...] = ()
    remove_spurious_issuer: bool = False


CORRECTIONS = (
    GoldCorrection(
        sample_id="N1130",
        ticker="SNCY",
        direction="negative",
        positive_strength=2,
        negative_strength=3,
        rationale=(
            "The selling-stockholder secondary and underwriter option create the "
            "dominant supply-overhang implication. The concurrent issuer repurchase "
            "is a material positive offset, but does not outweigh the principal event."
        ),
        review_policy="secondary_with_repurchase",
    ),
    GoldCorrection(
        sample_id="N0261",
        ticker="PEIX",
        direction="negative",
        positive_strength=0,
        negative_strength=3,
        rationale=(
            "An effective reverse split is a materially bearish listing and capital-structure "
            "signal even though it mechanically preserves enterprise value at effectiveness."
        ),
        review_policy="reverse_split",
    ),
    GoldCorrection(
        sample_id="N0774",
        ticker="SITE",
        direction="negative",
        positive_strength=2,
        negative_strength=3,
        rationale=(
            "Both EPS and sales missed consensus; year-over-year growth is a meaningful offset "
            "but does not outweigh the stronger benchmark misses."
        ),
    ),
    GoldCorrection(
        sample_id="N0850",
        ticker="JNPR",
        direction="positive",
        positive_strength=3,
        negative_strength=2,
        rationale=(
            "Both EPS and sales beat consensus; year-over-year declines are meaningful offsets "
            "but do not outweigh the stronger benchmark beats."
        ),
    ),
    GoldCorrection(
        sample_id="N1925",
        ticker="MOVE",
        direction="negative",
        positive_strength=0,
        negative_strength=3,
        rationale=(
            "An effective reverse split is a materially bearish listing and capital-structure "
            "signal even though it mechanically preserves enterprise value at effectiveness."
        ),
        review_policy="reverse_split",
    ),
)

HISTORICAL_RECAP_CORRECTIONS = (
    HistoricalRecapCorrection(
        sample_id="N0702",
        ticker="HIBB",
        content_role="automated_summary",
        communication_purpose="recap",
        eligibility_reason=(
            "Automated secondary earnings recap published after the article's own "
            "observed prior-day close; useful as issuer history, but not a fresh "
            "forecast or reaction trigger."
        ),
    ),
)


REVIEWED_SPEC_CORRECTIONS = (
    ReviewedSpecCorrection(
        "N0103", "WW", "mixed", 4, 3,
        "Debt reduction and lower interest expense materially improve financial flexibility, while bankruptcy disruption and other near-term headwinds remain material.",
        (
            ReplacementEvidence("capital.deleveraging", "We reduced our debt by more than 70%, freeing up approximately $50 million of cash annually from lower interest expense and are now relisted on NASDAQ under the ticker WW.", "positive", 4),
            ReplacementEvidence("operations.business_update", "While our strategic reorganization was a major milestone, we do face near-term headwinds, including residual noise from the bankruptcy process, which was acute in the second quarter.", "negative", 3),
        ),
    ),
    ReviewedSpecCorrection(
        "N0303", "M", "mixed", 3, 3,
        "Expected earnings and revenue growth are balanced by an Underweight downgrade, a lower price target, and operating concerns.",
        (
            ReplacementEvidence("earnings.performance", "For Q1, M is expected to report adjusted EPS of $0.36, up from $0.24 in the prior-year quarter, on revenue of $5.43 billion, according to third-party consensus estimates.", "positive", 3, statement_kind="forecast", epistemic_status="expected", time_relation="forward"),
            ReplacementEvidence("analyst.rating_action", "analysts from Morgan Stanley downgraded the stock from equal weight to underweight, dropping their price target from $27 to $25", "negative", 3, statement_kind="assessment"),
        ),
    ),
    ReviewedSpecCorrection(
        "N0905", "VST", "mixed", 4, 3,
        "Revenue declined and missed expectations, while adjusted EBITDA improved and management raised its outlook.",
        (
            ReplacementEvidence("earnings.performance", "operating revenue decline of 20.6% year-over-year to $4.09 billion", "negative", 3),
            ReplacementEvidence("guidance.issued", "Vistra anticipates FY23 adjusted EBITDA of $3.95 billion-$4.1 billion (prior $3.6 billion-$4 billion)", "positive", 4, statement_kind="forecast", epistemic_status="expected", time_relation="forward"),
        ),
    ),
    ReviewedSpecCorrection(
        "N0248", "KLDX", "neutral", 0, 0,
        "BMO initiated Klondex at Market Perform; the comparative discussion does not establish a directional issuer catalyst.",
        (ReplacementEvidence("analyst.rating_action", "BMO Capital Markets have initiated coverage on three underground mining companies namely Klondex Mines Ltd. (NYSE: KLDX), NEWMARKET GOLD INC COM NPV (OTC: NMKTF) and Richmont Mines Inc. (USA) (NYSE: RIC), with the first two being rated Market Perform and Richmont Mines started with an Outperform rating.", "neutral", 0, statement_kind="assessment"),),
    ),
    ReviewedSpecCorrection(
        "N0248", "NMKTF", "neutral", 0, 0,
        "BMO initiated Newmarket at Market Perform; gold-price leverage is contextual rather than directionally positive.",
        (ReplacementEvidence("analyst.rating_action", "BMO Capital Markets have initiated coverage on three underground mining companies namely Klondex Mines Ltd. (NYSE: KLDX), NEWMARKET GOLD INC COM NPV (OTC: NMKTF) and Richmont Mines Inc. (USA) (NYSE: RIC), with the first two being rated Market Perform and Richmont Mines started with an Outperform rating.", "neutral", 0, statement_kind="assessment"),),
    ),
    ReviewedSpecCorrection(
        "N0480", "BABA", "negative", 1, 2,
        "Apple's selection is a weak positive offset, but an 85% model-price cut is the stronger signal of intensifying competition.",
        (
            ReplacementEvidence("commercial.partnership", "Recently, Apple Inc (NASDAQ:AAPL) tapped Alibaba Group Holdings (NYSE:BABA) to bring AI features to iPhones in China", "positive", 1),
            ReplacementEvidence("commercial.competitive_position", "Alibaba cut Qwen-VL model prices by up to 85%", "negative", 2),
        ),
    ),
    ReviewedSpecCorrection(
        "N0589", "SQ", "mixed", 4, 3,
        "The EPS beat and reported growth are strongly positive, but adjusted sales guidance is materially below consensus.",
        (
            ReplacementEvidence("earnings.performance", "adjusted earnings of 25 cents per share, beating estimates by 5 cents.", "positive", 4),
            ReplacementEvidence("guidance.issued", "adjusted sales of $585 million to $595 million versus a $621 million estimate.", "negative", 3, statement_kind="forecast", epistemic_status="expected", time_relation="forward"),
        ),
    ),
    ReviewedSpecCorrection(
        "N0744", "TBPH", "mixed", 3, 4,
        "Revenue improved, but the company remained deeply loss-making and TD-8236 missed the primary objective of its Phase 2a study.",
        (
            ReplacementEvidence("earnings.performance", "Total revenue for the third quarter represents a $5.8 million increase over the same period in 2019.", "positive", 3),
            ReplacementEvidence("clinical.trial_result", "TD-8236 as a single agent did not meet the primary objective of the Phase 2a LAC study.", "negative", 4),
        ),
    ),
    ReviewedSpecCorrection(
        "N0818", "CRTD", "mixed", 4, 4,
        "Cash proceeds and debt reduction strengthened the balance sheet, while share issuance, conversions, and remaining warrants create material dilution risk.",
        (
            ReplacementEvidence("financial.liquidity", "the Company last week received over $7.8 in cash proceeds, significantly strengthening its balance sheet.", "positive", 4),
            ReplacementEvidence("capital.financing", "After factoring in the above transactions, Creatd now has approximately 16.3 million shares issued and outstanding. Additionally, there currently remain approximately 3 million outstanding privately-held warrants, the majority of which may be exercised at a price of $4.50, and 2.5 million publicly traded warrants having exercise prices of $4.50.", "negative", 4),
        ),
    ),
    ReviewedSpecCorrection(
        "N0915", "TLRY", "mixed", 3, 2,
        "The lower conversion price improves Tilray's conversion economics, while weaker liquidity protections increase transaction risk.",
        (
            ReplacementEvidence("capital.structure", "amend and restate the purchased note to reflect a reduction in Tilray Brands' conversion price from CA$0.85 to CA$0.40;", "positive", 3),
            ReplacementEvidence("financial.liquidity", "reduce the minimum liquidity interim covenant and closing condition from $100 million to CA$70 million ($54.13 million)", "negative", 2),
        ),
    ),
    ReviewedSpecCorrection(
        "N1184", "FEYE", "mixed", 3, 2,
        "The strategic investment supplies capital and external endorsement, while the convertible preferred security creates dilution risk.",
        (
            ReplacementEvidence("capital.financing", "FireEye, Inc. (NASDAQ:FEYE), the intelligence-led security company, today announced that the $400 million strategic investment led by funds managed by Blackstone Tactical Opportunities (\"Blackstone\") has closed.", "positive", 3),
            ReplacementEvidence("capital.structure", "The Series A Preferred will be convertible into shares of FireEye's common stock at a conversion price of $17.25 per share, subject to certain customary adjustments.", "negative", 2),
        ),
    ),
    ReviewedSpecCorrection(
        "N0086", "AMZN", None, 0, 0,
        "Amazon is only named as competitive context in a Target preview; no AMZN issuer event is reported.",
        remove_spurious_issuer=True,
    ),
    ReviewedSpecCorrection(
        "N0531", "BMY", "neutral", 0, 0,
        "BMY is mentioned only as the supplier of nivolumab in another issuer's trial; no new BMY-specific event is reported.",
        (ReplacementEvidence("product.milestone", "Bristol-Myers Squibb Co's (NYSE:BMY) Nivolumab, sold under the brand name Opdivo, is an anti-cancer medication used to treat several types of cancer.", "neutral", 0, statement_kind="background"),),
    ),
    ReviewedSpecCorrection(
        "N0815", "RDS.A", "neutral", 1, 1,
        "The unbenchmarked divestiture terms do not establish whether value realization is favorable or unfavorable.",
        (
            ReplacementEvidence("corporate_transaction.asset_sale", "Shell (NYSE: RDS.A) (NYSE:RDS.B) today announced it has reached a binding agreement to sell its Australia downstream businesses (excluding Aviation) to Vitol for a total transaction value of approximately A$2.9 billion (US$2.6 billion).", "negative", 1),
            ReplacementEvidence("commercial.partnership", "It also includes a brand license arrangement and an exclusive distributor arrangement in Australia for Shell Lubricants.", "positive", 1),
        ),
    ),
    ReviewedSpecCorrection(
        "N0815", "RDS.B", "neutral", 1, 1,
        "The unbenchmarked divestiture terms do not establish whether value realization is favorable or unfavorable.",
        (
            ReplacementEvidence("corporate_transaction.asset_sale", "Shell (NYSE: RDS.A) (NYSE:RDS.B) today announced it has reached a binding agreement to sell its Australia downstream businesses (excluding Aviation) to Vitol for a total transaction value of approximately A$2.9 billion (US$2.6 billion).", "negative", 1),
            ReplacementEvidence("commercial.partnership", "It also includes a brand license arrangement and an exclusive distributor arrangement in Australia for Shell Lubricants.", "positive", 1),
        ),
    ),
    *(
        ReviewedSpecCorrection(
            "N1748", ticker, "mixed", 2, 2,
            "The analyst names the issuer as a long-term favorite but expects near-term demand slowing and an inventory overshoot.",
            (
                ReplacementEvidence("analyst.issuer_assessment", "The report also highlights three favorite names in the long term: Altera Corp. (NASDAQ: ALTR), Qualcomm Inc. (NASDAQ: QCOM) and NetLogics Microsystems (NASDAQ: NETL).", "positive", 2, statement_kind="assessment", epistemic_status="expected", time_relation="forward"),
                ReplacementEvidence("analyst.issuer_assessment", "Based on these factors, we believe demand will slow and there will be an inventory overshoot in 2H, likely Q3.", "negative", 2, statement_kind="assessment", epistemic_status="expected", time_relation="forward"),
            ),
        )
        for ticker in ("ALTR", "NETL", "QCOM")
    ),
)


def apply_manual_gold_corrections() -> list[dict[str, Any]]:
    config = default_certification_config()
    pairs = {
        annotation_path.stem: (annotation_path, article_path)
        for annotation_path, article_path, _collection in discover_pairs(config.collection_roots)
    }
    changes: list[dict[str, Any]] = []
    touched_roots: set[Path] = set()
    reviewed_spec_sample_ids: set[str] = set()
    for correction in CORRECTIONS:
        if correction.sample_id not in pairs:
            raise RuntimeError(f"Missing manual gold source for {correction.sample_id}")
        annotation_path, article_path = pairs[correction.sample_id]
        article = read_json(article_path)
        annotation = read_json(annotation_path)
        old_hash = str(annotation.get("annotation_sha256") or "")
        _correct_annotation(annotation, article, correction)
        write_json_atomic(annotation_path, annotation)
        touched_roots.add(annotation_path.parent.parent)

        spec_path = config.output_root / "reviewed_specs" / f"{correction.sample_id}.json"
        spec = read_json(spec_path)
        _correct_review_spec(spec, correction)
        write_json_atomic(spec_path, spec)
        changes.append({
            "sample_id": correction.sample_id,
            "ticker": correction.ticker,
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": annotation["annotation_sha256"],
            "semantic_direction": correction.direction,
            "positive_evidence_level": correction.positive_strength,
            "negative_evidence_level": correction.negative_strength,
        })

    for correction in HISTORICAL_RECAP_CORRECTIONS:
        if correction.sample_id not in pairs:
            raise RuntimeError(f"Missing manual gold source for {correction.sample_id}")
        annotation_path, article_path = pairs[correction.sample_id]
        article = read_json(article_path)
        annotation = read_json(annotation_path)
        old_hash = str(annotation.get("annotation_sha256") or "")
        _correct_historical_recap_annotation(annotation, article, correction)
        write_json_atomic(annotation_path, annotation)
        touched_roots.add(annotation_path.parent.parent)

        spec_path = config.output_root / "reviewed_specs" / f"{correction.sample_id}.json"
        spec = read_json(spec_path)
        _correct_historical_recap_review_spec(spec, correction)
        write_json_atomic(spec_path, spec)
        changes.append({
            "sample_id": correction.sample_id,
            "ticker": correction.ticker,
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": annotation["annotation_sha256"],
            "content_role": correction.content_role,
            "forecast_trigger_eligible": False,
            "reaction_evaluation_eligible": False,
            "issuer_history_context_eligible": True,
        })

    for correction in REVIEWED_SPEC_CORRECTIONS:
        if correction.sample_id not in pairs:
            raise RuntimeError(f"Missing manual gold source for {correction.sample_id}")
        annotation_path, article_path = pairs[correction.sample_id]
        article = read_json(article_path)
        annotation = read_json(annotation_path)
        _validate_reviewed_source_alignment(annotation, correction)

        spec_path = config.output_root / "reviewed_specs" / f"{correction.sample_id}.json"
        spec = read_json(spec_path)
        _replace_review_spec_issuer_evidence(spec, correction)
        compiled = compile_review_spec(article, spec)
        _validate_compiled_review_spec(compiled, correction)
        write_json_atomic(spec_path, spec)
        reviewed_spec_sample_ids.add(correction.sample_id)
        changes.append({
            "sample_id": correction.sample_id,
            "ticker": correction.ticker,
            "semantic_direction": correction.direction,
            "positive_evidence_level": correction.positive_strength,
            "negative_evidence_level": correction.negative_strength,
            "removed_spurious_issuer": correction.remove_spurious_issuer,
        })

    _recertify_reviewed_spec_corrections(
        config=config,
        pairs=pairs,
        sample_ids=reviewed_spec_sample_ids,
    )

    for root in sorted(touched_roots):
        refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION_V3)
    return changes


def _recertify_reviewed_spec_corrections(
    *,
    config: Any,
    pairs: Mapping[str, tuple[Path, Path]],
    sample_ids: set[str],
) -> None:
    reviews: list[dict[str, Any]] = []
    articles: dict[str, dict[str, Any]] = {}
    for sample_id in sorted(sample_ids):
        if sample_id not in pairs:
            raise RuntimeError(f"Missing manual gold source for {sample_id}")
        _annotation_path, article_path = pairs[sample_id]
        article = read_json(article_path)
        spec = read_json(config.output_root / "reviewed_specs" / f"{sample_id}.json")
        document = compile_review_spec(article, spec)
        articles[sample_id] = article
        reviews.append({
            "sample_id": sample_id,
            "review_notes": str(spec["review_notes"]),
            "document": document,
        })
    if not reviews:
        return
    certified = certify_documents(
        config,
        reviews,
        reviewer="Codex source-bound gold correction v3",
    )
    audit_root = config.output_root / "certified_reviews"
    audit_root.mkdir(parents=True, exist_ok=True)
    for document in certified:
        target = audit_root / f"{document['sample_id']}.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(
            render_review_packet(
                articles[str(document["sample_id"])],
                document,
                certified=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(target)


def _validate_reviewed_source_alignment(
    annotation: Mapping[str, Any],
    correction: ReviewedSpecCorrection,
) -> None:
    units = [
        unit for unit in annotation.get("issuer_units", [])
        if str(unit.get("ticker") or "").upper() == correction.ticker
    ]
    if correction.remove_spurious_issuer:
        if units:
            raise RuntimeError(
                f"Reviewed source unexpectedly contains {correction.sample_id}/{correction.ticker}"
            )
        return
    if len(units) != 1:
        raise RuntimeError(
            f"Expected one reviewed-source {correction.ticker} unit in "
            f"{correction.sample_id}, found {len(units)}"
        )
    unit = units[0]
    actual = (
        str(unit.get("semantic_direction") or ""),
        int(unit.get("positive_evidence_level") or 0),
        int(unit.get("negative_evidence_level") or 0),
    )
    expected = (
        correction.direction,
        correction.positive_strength,
        correction.negative_strength,
    )
    if actual != expected:
        raise RuntimeError(
            f"Reviewed-source decision changed for {correction.sample_id}/{correction.ticker}: "
            f"expected={expected} actual={actual}"
        )


def _replace_review_spec_issuer_evidence(
    spec: dict[str, Any],
    correction: ReviewedSpecCorrection,
) -> None:
    if str(spec.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Review-spec identity mismatch for {correction.sample_id}")
    ticker = correction.ticker.upper()
    matching_entities = [
        entity for entity in spec.get("entities", [])
        if _review_spec_entity_ticker(entity) == ticker
    ]
    if correction.remove_spurious_issuer and not matching_entities:
        note = f"{CORRECTION_VERSION}: {ticker}: {correction.rationale}"
        existing_notes = str(spec.get("review_notes") or "").strip()
        if note not in existing_notes:
            spec["review_notes"] = " ".join(filter(None, (existing_notes, note)))
        return
    if len(matching_entities) != 1:
        raise RuntimeError(
            f"Expected one review-spec entity for {correction.sample_id}/{ticker}, "
            f"found {len(matching_entities)}"
        )
    entity = matching_entities[0]
    entity_id = _review_spec_entity_id(entity, ticker)

    retained_statements: list[dict[str, Any]] = []
    for statement in spec.get("statements", []):
        participations = list(statement.get("participations", []))
        retained = [
            row for row in participations
            if not _participation_targets_entity(row, ticker, entity_id)
        ]
        if len(retained) == len(participations):
            retained_statements.append(statement)
            continue
        if retained:
            statement["participations"] = retained
            retained_statements.append(statement)
    spec["statements"] = retained_statements
    spec["issuer_view_overrides"] = [
        row for row in spec.get("issuer_view_overrides", [])
        if str(row.get("entity_id") or "") != entity_id
    ]
    spec["observed_market_moves"] = [
        row for row in spec.get("observed_market_moves", [])
        if str(row.get("ticker") or "").upper() != ticker
    ]

    if correction.remove_spurious_issuer:
        spec["entities"] = [row for row in spec.get("entities", []) if row is not entity]
    else:
        for evidence in correction.replacement_evidence:
            spec["statements"].append({
                "statement_kind": evidence.statement_kind,
                "concept_leaf": evidence.concept_leaf,
                "epistemic_status": evidence.epistemic_status,
                "time_relation": evidence.time_relation,
                "evidence": [{"quote": evidence.evidence, "occurrence": 1}],
                "participations": [{
                    "ticker": ticker,
                    "semantic_role": "affected_subject",
                    "discourse_role": "none",
                    "semantic_sentiment": evidence.semantic_sentiment,
                    "sentiment_strength": evidence.sentiment_strength,
                }],
            })
    note = f"{CORRECTION_VERSION}: {ticker}: {correction.rationale}"
    existing_notes = str(spec.get("review_notes") or "").strip()
    if note not in existing_notes:
        spec["review_notes"] = " ".join(filter(None, (existing_notes, note)))


def _validate_compiled_review_spec(
    document: Mapping[str, Any],
    correction: ReviewedSpecCorrection,
) -> None:
    tickers = {
        str(row["entity_id"]): str(row.get("ticker") or "").upper()
        for row in document.get("entities", [])
    }
    views = [
        row for row in document.get("issuer_views", [])
        if tickers.get(str(row.get("entity_id") or "")) == correction.ticker
    ]
    if correction.remove_spurious_issuer:
        if views or correction.ticker in tickers.values():
            raise RuntimeError(
                f"Spurious issuer remains in {correction.sample_id}/{correction.ticker}"
            )
        return
    if len(views) != 1:
        raise RuntimeError(
            f"Expected one compiled issuer view for {correction.sample_id}/"
            f"{correction.ticker}, found {len(views)}"
        )
    view = views[0]
    actual = (
        str(view.get("composite_sentiment") or ""),
        int(view.get("positive_strength") or 0),
        int(view.get("negative_strength") or 0),
    )
    expected = (
        correction.direction,
        correction.positive_strength,
        correction.negative_strength,
    )
    if actual != expected:
        raise RuntimeError(
            f"Compiled correction mismatch for {correction.sample_id}/{correction.ticker}: "
            f"expected={expected} actual={actual}"
        )


def _review_spec_entity_ticker(entity: Any) -> str:
    if isinstance(entity, str):
        return entity.upper()
    return str(entity.get("ticker") or "").upper()


def _review_spec_entity_id(entity: Any, ticker: str) -> str:
    if isinstance(entity, Mapping):
        return str(entity.get("entity_id") or f"security:{ticker}")
    return f"security:{ticker}"


def _participation_targets_entity(
    participation: Mapping[str, Any],
    ticker: str,
    entity_id: str,
) -> bool:
    requested_id = str(participation.get("entity_id") or "").upper()
    requested_ticker = str(participation.get("ticker") or "").upper()
    return requested_ticker == ticker or requested_id in {ticker, entity_id.upper()}


def _correct_annotation(
    annotation: dict[str, Any],
    article: Mapping[str, Any],
    correction: GoldCorrection,
) -> None:
    if str(annotation.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Annotation identity mismatch for {correction.sample_id}")
    units = [
        unit for unit in annotation.get("issuer_units", [])
        if str(unit.get("ticker") or "").upper() == correction.ticker
    ]
    if len(units) != 1:
        raise RuntimeError(
            f"Expected one {correction.ticker} unit in {correction.sample_id}, found {len(units)}"
        )
    unit = units[0]
    unit.update({
        "semantic_direction": correction.direction,
        "positive_evidence_level": correction.positive_strength,
        "negative_evidence_level": correction.negative_strength,
        "semantic_rationale": correction.rationale,
    })
    note = f"{CORRECTION_VERSION}: corrected overall direction to {correction.direction}."
    existing_notes = str(annotation.get("review_notes") or "").strip()
    if note not in existing_notes:
        annotation["review_notes"] = " ".join(filter(None, (existing_notes, note)))
        annotation["review_round"] = int(annotation.get("review_round") or 1) + 1
    annotation.pop("annotation_sha256", None)
    materialize_evidence_spans(annotation, article)
    validation = validate_annotation(annotation, expected_item=article)
    if not validation.valid:
        raise RuntimeError(
            f"Invalid corrected annotation {correction.sample_id}: {validation.errors}"
        )
    annotation["annotation_sha256"] = stable_json_hash(annotation)


def _correct_review_spec(spec: dict[str, Any], correction: GoldCorrection) -> None:
    if str(spec.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Review-spec identity mismatch for {correction.sample_id}")
    found_negative = False
    found_positive = False
    found_reverse_split = False
    for statement in spec.get("statements", []):
        concept = str(statement.get("concept_leaf") or "")
        evidence_text = " ".join(
            str(value.get("quote") if isinstance(value, Mapping) else value)
            for value in statement.get("evidence", [])
        ).casefold()
        for participation in statement.get("participations", []):
            if str(participation.get("entity_id") or "") != f"security:{correction.ticker}":
                continue
            if concept == "capital.financing" and "secondary public offering" in evidence_text:
                participation.update({
                    "semantic_sentiment": "negative",
                    "sentiment_strength": correction.negative_strength,
                })
                found_negative = True
            if concept == "capital.return" and "$5 million" in evidence_text:
                participation.update({
                    "semantic_sentiment": "positive",
                    "sentiment_strength": correction.positive_strength,
                })
                found_positive = True
            if (
                correction.review_policy == "reverse_split"
                and concept in {"listing.market_structure", "capital.structure"}
                and "reverse" in evidence_text
                and "split" in evidence_text
            ):
                participation.update({
                    "semantic_sentiment": "negative",
                    "sentiment_strength": correction.negative_strength,
                })
                found_reverse_split = True
            if correction.review_policy == "evidence_balance":
                if participation.get("semantic_sentiment") == "negative":
                    found_negative = True
                if participation.get("semantic_sentiment") == "positive":
                    found_positive = True
    if correction.review_policy == "secondary_with_repurchase" and (
        not found_negative or not found_positive
    ):
        raise RuntimeError(
            f"Could not find both dominant and offsetting evidence in {correction.sample_id} review spec"
        )
    if correction.review_policy == "reverse_split" and not found_reverse_split:
        raise RuntimeError(
            f"Could not find reverse-split evidence in {correction.sample_id} review spec"
        )
    if correction.review_policy == "evidence_balance" and (
        not found_negative or not found_positive
    ):
        raise RuntimeError(
            f"Could not find both benchmark and prior-period evidence in {correction.sample_id} review spec"
        )
    spec["issuer_view_overrides"] = [{
        "entity_id": f"security:{correction.ticker}",
        "composite_sentiment": correction.direction,
        "reason": correction.rationale,
    }]
    note = f"{CORRECTION_VERSION}: {correction.rationale}"
    existing_notes = str(spec.get("review_notes") or "").strip()
    if note not in existing_notes:
        spec["review_notes"] = " ".join(filter(None, (existing_notes, note)))


def _correct_historical_recap_annotation(
    annotation: dict[str, Any],
    article: Mapping[str, Any],
    correction: HistoricalRecapCorrection,
) -> None:
    if str(annotation.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Annotation identity mismatch for {correction.sample_id}")
    units = [
        unit for unit in annotation.get("issuer_units", [])
        if str(unit.get("ticker") or "").upper() == correction.ticker
    ]
    if len(units) != 1:
        raise RuntimeError(
            f"Expected one {correction.ticker} unit in {correction.sample_id}, found {len(units)}"
        )
    annotation["content_role"] = correction.content_role
    unit = units[0]
    unit.update({
        "time_orientation": "historical",
        "forecast_trigger_eligible": False,
        "reaction_evaluation_eligible": False,
        "issuer_history_context_eligible": True,
        "eligibility_reason": correction.eligibility_reason,
    })
    note = (
        f"{CORRECTION_VERSION}: corrected {correction.sample_id} to a historical "
        "automated recap; forecast and reaction eligibility are false."
    )
    existing_notes = str(annotation.get("review_notes") or "").strip()
    if note not in existing_notes:
        annotation["review_notes"] = " ".join(filter(None, (existing_notes, note)))
        annotation["review_round"] = int(annotation.get("review_round") or 1) + 1
    annotation.pop("annotation_sha256", None)
    materialize_evidence_spans(annotation, article)
    validation = validate_annotation(annotation, expected_item=article)
    if not validation.valid:
        raise RuntimeError(
            f"Invalid corrected annotation {correction.sample_id}: {validation.errors}"
        )
    annotation["annotation_sha256"] = stable_json_hash(annotation)


def _correct_historical_recap_review_spec(
    spec: dict[str, Any],
    correction: HistoricalRecapCorrection,
) -> None:
    if str(spec.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Review-spec identity mismatch for {correction.sample_id}")
    spec.setdefault("envelope", {})["communication_purpose"] = correction.communication_purpose
    matched = 0
    for statement in spec.get("statements", []):
        if str(statement.get("statement_kind")) != "event":
            continue
        if str(statement.get("concept_leaf")) != "earnings.performance":
            continue
        if any(
            str(row.get("entity_id") or "") == f"security:{correction.ticker}"
            for row in statement.get("participations", [])
        ):
            statement["time_relation"] = "historical"
            matched += 1
    if not matched:
        raise RuntimeError(
            f"Could not find historical earnings evidence in {correction.sample_id} review spec"
        )
    note = f"{CORRECTION_VERSION}: {correction.eligibility_reason}"
    existing_notes = str(spec.get("review_notes") or "").strip()
    if note not in existing_notes:
        spec["review_notes"] = " ".join(filter(None, (existing_notes, note)))

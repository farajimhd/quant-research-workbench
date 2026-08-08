from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Mapping


POLICY_VERSION = "news_synthesis_eligibility_v1"
SYNTHESIS_VERSION = "news_synthesis_renderer_v1"


def derive_issuer_views(
    entities: Iterable[Mapping[str, Any]],
    participations: Iterable[Mapping[str, Any]],
    *,
    statements: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    issuer_ids = {str(row["entity_id"]) for row in entities if row.get("entity_kind") in {"issuer", "security"}}
    by_entity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in participations:
        if row.get("entity_id") in issuer_ids:
            by_entity[str(row["entity_id"])].append(row)
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    views: list[dict[str, Any]] = []
    for entity_id in sorted(by_entity):
        rows = by_entity[entity_id]
        directional_rows = rows if not statement_by_id else [
            row
            for row in rows
            if _directionally_current(
                statement_by_id.get(str(row.get("statement_id") or ""), {})
            )
        ]
        directional_statement_ids = {
            str(row.get("statement_id") or "") for row in directional_rows
        }
        positive = max((int(row["sentiment_strength"]) for row in directional_rows if row["semantic_sentiment"] == "positive"), default=0)
        negative = max((int(row["sentiment_strength"]) for row in directional_rows if row["semantic_sentiment"] == "negative"), default=0)
        positive_ids = sorted({
            str(row["statement_id"])
            for row in directional_rows
            if row["semantic_sentiment"] == "positive"
        })
        negative_ids = sorted({
            str(row["statement_id"])
            for row in directional_rows
            if row["semantic_sentiment"] == "negative"
        })
        directional_packages: dict[str, dict[str, int]] = {
            "positive": {},
            "negative": {},
        }
        for row in directional_rows:
            direction = str(row.get("semantic_sentiment") or "")
            if direction not in directional_packages:
                continue
            statement = statement_by_id.get(str(row["statement_id"]), {})
            # Count economic facts, not renderings. Headlines, teasers, body
            # sentences, tables, and sibling concepts often restate the same
            # metric or transaction. A canonical event/metric key keeps those
            # repetitions from voting repeatedly while preserving genuinely
            # distinct financial metrics and event classes.
            package_key = _evidence_package_key(statement)
            directional_packages[direction][package_key] = max(
                directional_packages[direction].get(package_key, 0),
                int(row.get("sentiment_strength") or 0),
            )
        positive_score = sum(directional_packages["positive"].values())
        negative_score = sum(directional_packages["negative"].values())
        neutral_ids = sorted({
            str(row["statement_id"])
            for row in rows
            if row["semantic_sentiment"] == "neutral"
            or str(row.get("statement_id") or "") not in directional_statement_ids
        })
        guidance_relations = [
            str(fact.get("relation"))
            for row in rows
            if (statement := statement_by_id.get(str(row["statement_id"])))
            and statement.get("concept_leaf") == "guidance.issued"
            for fact in statement.get("typed_facts", ())
            if fact.get("fact_type") == "estimate_comparison"
            and fact.get("subject_role") == "issuer_guidance"
            and fact.get("comparator_role") in {
                "consensus_estimate",
                "management_guidance",
            }
        ]
        below = guidance_relations.count("below")
        above = guidance_relations.count("above")
        issuer_statements = [
            statement_by_id.get(str(row["statement_id"]), {}) for row in rows
        ]
        ipo_quotes = [
            str((statement.get("evidence_spans") or [{}])[0].get("quote", ""))
            for statement in issuer_statements
            if statement.get("concept_leaf") == "capital.financing"
            and re.search(r"\b(?:initial public offering|IPO)\b", str((statement.get("evidence_spans") or [{}])[0].get("quote", "")), re.I)
            and re.match(r"\s*Title:", str((statement.get("evidence_spans") or [{}])[0].get("quote", "")), re.I)
        ]
        all_ipo_quotes = [
            str((statement.get("evidence_spans") or [{}])[0].get("quote", ""))
            for statement in issuer_statements
            if statement.get("concept_leaf") == "capital.financing"
            and re.search(r"\b(?:initial public offering|IPO)\b", str((statement.get("evidence_spans") or [{}])[0].get("quote", "")), re.I)
        ]
        unbenchmarked_estimated_ads_ipo = bool(ipo_quotes) and all(
            re.search(
                r"\bestimat(?:e|es|ed|ing)\b.{0,100}\bIPO price\b"
                r".{0,100}\b(?:ADS|ADRs?)\b",
                quote,
                re.I,
            )
            for quote in ipo_quotes
        )
        negative_current_statement_ids = {
            str(row.get("statement_id") or "")
            for row in directional_rows
            if row.get("semantic_sentiment") == "negative"
        }
        negative_financing_statement_ids = {
            str(row.get("statement_id") or "")
            for row in directional_rows
            if row.get("semantic_sentiment") == "negative"
            and statement_by_id.get(str(row.get("statement_id") or ""), {}).get("concept_leaf")
            == "capital.financing"
        }
        current_financing_text = " ".join(
            str((statement.get("evidence_spans") or [{}])[0].get("quote", ""))
            for statement in issuer_statements
            if _directionally_current(statement)
        )
        financing_benefit = bool(
            (
                re.search(r"\bconvertible preferred stock\b", current_financing_text, re.I)
                and re.search(r"\bimmediate access to\b.{0,80}\bliquidity\b", current_financing_text, re.I)
            )
            or (
                re.search(r"\bprivate placement\b", current_financing_text, re.I)
                and re.search(
                    r"\bnet proceeds\b.{0,180}\bworking capital\b.{0,180}"
                    r"\bexpan(?:d|ds|ded|ding|sion)\b.{0,80}\boperations?\b",
                    current_financing_text,
                    re.I,
                )
            )
            or (
                re.search(
                    r"\bstrategic investment\b.{0,100}\bin lieu of\b.{0,100}\boffering\b|"
                    r"\bin lieu of\b.{0,100}\boffering\b.{0,100}\bstrategic investment\b",
                    current_financing_text,
                    re.I,
                )
                and re.search(
                    r"\bstrategic investor\b.{0,120}\b(?:invest(?:s|ed|ment)|capital)\b",
                    current_financing_text,
                    re.I,
                )
            )
        )
        financing_tradeoff = bool(
            negative_financing_statement_ids and financing_benefit
        )
        benchmarked_result_packages: dict[str, dict[str, int]] = {
            "positive": {},
            "negative": {},
        }
        for row in directional_rows:
            direction = str(row.get("semantic_sentiment") or "")
            if direction not in benchmarked_result_packages:
                continue
            statement = statement_by_id.get(str(row.get("statement_id") or ""), {})
            if statement.get("concept_leaf") not in {
                "earnings.performance",
                "financial.operating_performance",
            }:
                continue
            count = _benchmarked_result_cue_count(statement, direction)
            if count:
                key = _evidence_package_key(statement)
                benchmarked_result_packages[direction][key] = max(
                    benchmarked_result_packages[direction].get(key, 0),
                    count,
                )
        benchmarked_result_positive = sum(
            benchmarked_result_packages["positive"].values()
        )
        benchmarked_result_negative = sum(
            benchmarked_result_packages["negative"].values()
        )
        coordinated_financial_tradeoff = _coordinated_financial_tradeoff(
            directional_rows,
            statement_by_id,
        )
        legal_terminal_tradeoff = _legal_terminal_tradeoff(
            directional_rows,
            statement_by_id,
        )
        regulatory_challenge_tradeoff = _regulatory_challenge_tradeoff(
            directional_rows,
            statement_by_id,
        )
        clinical_reassessment_tradeoff = _clinical_reassessment_tradeoff(
            directional_rows,
            statement_by_id,
        )
        negative_guidance_conflict = any(
            row.get("semantic_sentiment") == "negative"
            and int(row.get("sentiment_strength") or 0) >= 2
            and statement_by_id.get(str(row.get("statement_id") or ""), {}).get("concept_leaf")
            == "guidance.issued"
            for row in directional_rows
        )
        positive_guidance_conflict = any(
            row.get("semantic_sentiment") == "positive"
            and int(row.get("sentiment_strength") or 0) >= 2
            and statement_by_id.get(str(row.get("statement_id") or ""), {}).get("concept_leaf")
            == "guidance.issued"
            for row in directional_rows
        )
        focal_adverse_guidance = bool(below and not above) and any(
            re.search(
                r"^\s*(?:Title|Teaser):.{0,180}"
                r"(?:\b(?:weak|weaker|lower(?:s|ed)?|cut|cuts|disappointing|"
                r"below)\b.{0,80}\b(?:guidance|outlook|forecasts?)\b|"
                r"\b(?:guidance|outlook|forecasts?)\b.{0,100}"
                r"\b(?:shares?\s+(?:down|lower)|plung(?:e|es|ed)|tumbles?|crushed)\b)",
                str((statement.get("evidence_spans") or [{}])[0].get("quote", "")),
                re.I,
            )
            for statement in issuer_statements
        )
        positive_material_offset = any(
            row.get("semantic_sentiment") == "positive"
            and int(row.get("sentiment_strength") or 0) >= 2
            and statement_by_id.get(str(row.get("statement_id") or ""), {}).get("concept_leaf")
            in {
                "financial.cash_flow",
                "financial.margin",
                "guidance.issued",
                "operations.cost_efficiency",
            }
            for row in directional_rows
        )
        distress_restructuring = any(
            str(statement.get("statement_id") or "") in negative_current_statement_ids
            and
            statement.get("concept_leaf") in {
                "operations.business_update",
                "credit.solvency",
            }
            and re.search(
                r"\b(?:restructuring support agreement|pre[- ]negotiated restructuring|"
                r"bankruptcy filing|file[sd]? for chapter 11|"
                r"initiat(?:e[sd]?|ing) (?:voluntary )?proceedings under chapter 11)\b",
                str((statement.get("evidence_spans") or [{}])[0].get("quote", "")),
                re.I,
            )
            for statement in issuer_statements
        )
        current_adverse_regulatory_blocker = any(
            row.get("semantic_sentiment") == "negative"
            and int(row.get("sentiment_strength") or 0) >= 2
            and _is_active_regulatory_blocker(
                statement_by_id.get(str(row.get("statement_id") or ""), {})
            )
            for row in directional_rows
        )
        current_favorable_regulatory_outcome = any(
            row.get("semantic_sentiment") == "positive"
            and int(row.get("sentiment_strength") or 0) >= 2
            and _is_current_favorable_regulatory_outcome(
                statement_by_id.get(str(row.get("statement_id") or ""), {})
            )
            for row in directional_rows
        )
        if legal_terminal_tradeoff or regulatory_challenge_tradeoff or clinical_reassessment_tradeoff:
            sentiment = "mixed"
            positive = max(positive, 2)
            negative = max(negative, 2)
        elif (
            current_adverse_regulatory_blocker
            and not current_favorable_regulatory_outcome
        ):
            # An active regulator-imposed blocker is the controlling current
            # event until an actual favorable regulator disposition exists.
            # Historical trial success, management confidence, cost actions,
            # or unchanged guidance are mitigation, not resolution.
            sentiment = "negative"
            negative = max(negative, 3)
        elif focal_adverse_guidance:
            # When the source itself identifies below-benchmark forward
            # guidance as the focal event, historical realized beats are
            # context rather than an equal current offset. The numeric fact is
            # required so qualitative headline language alone cannot control.
            sentiment = "negative"
            negative = max(negative, 3)
        elif distress_restructuring:
            sentiment = "negative"
            negative = max(negative, 3)
        elif financing_tradeoff:
            sentiment = "mixed"
            positive = max(positive, 2)
            negative = max(negative, 2)
        elif coordinated_financial_tradeoff and not negative_guidance_conflict:
            # Adjacent metric-specific realized results are one coordinated
            # package. Material beats and deteriorations both remain visible
            # instead of being outvoted by duplicate concept renderings.
            sentiment = "mixed"
            positive = max(positive, 3)
            negative = max(negative, 3)
        elif unbenchmarked_estimated_ads_ipo:
            sentiment = "neutral"
            positive = 0
            negative = 0
        elif ipo_quotes and any(re.search(r"\babove\b.{0,50}\b(?:expected )?(?:price )?range\b", quote, re.I) for quote in all_ipo_quotes):
            sentiment = "positive"
            positive = max(positive, 3)
        elif ipo_quotes:
            sentiment = "mixed"
            positive = max(positive, 2)
            negative = max(negative, 2)
        elif below >= 2 and not above:
            sentiment = "negative"
            negative = max(negative, 3)
        elif above >= 2 and not below:
            sentiment = "positive"
            positive = max(positive, 3)
        elif (
            benchmarked_result_positive >= 2
            and not benchmarked_result_negative
            and not negative_guidance_conflict
        ):
            sentiment = "positive"
            positive = max(positive, 3)
        elif (
            benchmarked_result_negative >= 2
            and not benchmarked_result_positive
            and not positive_guidance_conflict
            and not positive_material_offset
        ):
            sentiment = "negative"
            negative = max(negative, 3)
        elif (
            positive_score >= negative_score + 2
            and positive_score * 2 >= negative_score * 3
            and positive > 0
        ):
            sentiment = "positive"
        elif (
            negative_score >= positive_score + 2
            and negative_score * 2 >= positive_score * 3
            and negative > 0
        ):
            sentiment = "negative"
        elif (
            positive_score >= max(12, negative_score * 2)
            and positive > negative
        ):
            sentiment = "positive"
        elif (
            negative_score >= max(12, positive_score * 2)
            and negative > positive
        ):
            sentiment = "negative"
        elif positive and negative and min(positive, negative) >= 2 and abs(positive - negative) <= 1:
            sentiment = "mixed"
        elif positive > negative:
            sentiment = "positive"
        elif negative > positive:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        views.append(
            {
                "entity_id": entity_id,
                "composite_sentiment": sentiment,
                "positive_strength": positive,
                "negative_strength": negative,
                "statement_ids": sorted({str(row["statement_id"]) for row in rows}),
                "positive_statement_ids": positive_ids,
                "negative_statement_ids": negative_ids,
                "neutral_statement_ids": neutral_ids,
            }
        )
    return views


def _directionally_current(statement: Mapping[str, Any]) -> bool:
    if statement.get("statement_kind") == "background":
        return False
    return statement.get("time_relation") != "historical"


def _is_current_favorable_regulatory_outcome(
    statement: Mapping[str, Any],
) -> bool:
    if statement.get("concept_leaf") not in {
        "clinical.regulatory_milestone",
        "regulatory.action",
    }:
        return False
    quote = " ".join(
        str(span.get("quote") or "")
        for span in statement.get("evidence_spans", ())
    )
    # Require an actual regulator disposition. Regulatory vocabulary inside
    # management commentary (for example, an "FDA-approved comparator") is
    # not evidence that the current blocker was resolved.
    return bool(re.search(
        r"\b(?:fda(?![- ]approved\b)|ema|mhra|health canada|"
        r"european commission|regulator\w*|agency)\b.{0,80}"
        r"\b(?:approv(?:es|ed)|authoriz(?:es|ed)|clear(?:s|ed)|grant(?:s|ed)|"
        r"accept(?:s|ed)|acknowledg(?:es|ed)|lift(?:s|ed)|remov(?:es|ed))\b|"
        r"\b(?:clinical hold|refusal[- ]to[- ]file|complete response letter)\b"
        r".{0,80}\b(?:lift(?:ed)?|remov(?:ed)?|withdrawn|resolved)\b",
        quote,
        re.I,
    ))


def _is_active_regulatory_blocker(statement: Mapping[str, Any]) -> bool:
    if statement.get("concept_leaf") not in {
        "clinical.regulatory_milestone",
        "regulatory.action",
    }:
        return False
    quote = " ".join(
        str(span.get("quote") or "")
        for span in statement.get("evidence_spans", ())
    )
    focal_assertion = bool(
        re.match(r"\s*(?:Title|Teaser):", quote, re.I)
        or re.search(
            r"\b(?:today|currently)\b.{0,80}\b(?:announc(?:e[sd]?|ing)|"
            r"report(?:s|ed|ing)?|notif(?:y|ies|ied|ying))\b.{0,120}"
            r"\b(?:refusal[- ]to[- ]file|complete response letter|clinical hold|"
            r"not approvable)\b",
            quote,
            re.I,
        )
    )
    if not focal_assertion:
        return False
    if re.search(
        r"\b(?:lift(?:s|ed)|remov(?:es|ed)|withdrawn|resolved|dismiss(?:es|ed))\b"
        r".{0,80}\b(?:clinical hold|refusal[- ]to[- ]file|complete response letter)\b|"
        r"\b(?:clinical hold|refusal[- ]to[- ]file|complete response letter)\b"
        r".{0,80}\b(?:lift(?:s|ed)|remov(?:es|ed)|withdrawn|resolved|dismiss(?:es|ed))\b",
        quote,
        re.I,
    ):
        return False
    return bool(re.search(
        r"\b(?:refusal[- ]to[- ]file(?: letter)?|refus(?:e|es|ed) to file|"
        r"not approvable)\b|"
        r"\b(?:fda|ema|regulator\w*|agency)\b.{0,80}"
        r"\breject(?:s|ed|ing)?\b.{0,80}"
        r"\b(?:application|submission|resubmission|filing)\b|"
        r"\b(?:receiv(?:e[sd]?|ing)|obtain(?:s|ed|ing)?|issu(?:e[sd]?|ing)|"
        r"send(?:s|ing)?|sent|deliver(?:s|ed|ing)?)\b.{0,80}"
        r"\bcomplete response letter\b|"
        r"\bcomplete response letter\b.{0,80}"
        r"\b(?:receiv(?:e[sd]?|ing)|obtain(?:s|ed|ing)?|issu(?:e[sd]?|ing)|"
        r"send(?:s|ing)?|sent|deliver(?:s|ed|ing)?)\b|"
        r"\b(?:place[sd]?|impose[sd]?|remain(?:s|ed)?|continue[sd]?)\b.{0,60}"
        r"\bclinical hold\b|"
        r"\bclinical hold\b.{0,60}\b(?:place[sd]?|impose[sd]?|remain(?:s|ed)?|"
        r"continue[sd]?|in effect)\b|"
        r"\b(?:panel|committee)\b.{0,80}\b(?:votes? against|does not recommend)\b|"
        r"\b(?:additional|supplemental)\b.{0,40}\b(?:data|information)\b"
        r".{0,60}\b(?:required|requested|needed)\b",
        quote,
        re.I,
    ))


def _coordinated_financial_tradeoff(
    rows: Iterable[Mapping[str, Any]],
    statement_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    financial: list[tuple[str, int, int, str]] = []
    concepts = {"earnings.performance", "financial.operating_performance"}
    for row in rows:
        direction = str(row.get("semantic_sentiment") or "")
        if direction not in {"positive", "negative"}:
            continue
        if int(row.get("sentiment_strength") or 0) < 3:
            continue
        statement = statement_by_id.get(str(row.get("statement_id") or ""), {})
        if statement.get("concept_leaf") not in concepts:
            continue
        span = (statement.get("evidence_spans") or [{}])[0]
        quote = str(span.get("quote") or "")
        if not re.search(
            r"\b(?:vs\.?|versus|up from|down from|compared (?:with|to)|"
            r"beats?|miss(?:es|ed)?)\b",
            quote,
            re.I,
        ):
            continue
        financial.append((
            direction,
            int(span.get("start") or 0),
            int(span.get("end") or 0),
            str(span.get("source_field") or ""),
        ))
    for left in financial:
        for right in financial:
            if left[0] == right[0] or left[3] != right[3] or left[1] == right[1]:
                continue
            gap = max(left[1], right[1]) - min(left[2], right[2])
            if gap <= 4:
                return True
    return False


def _legal_terminal_tradeoff(
    rows: Iterable[Mapping[str, Any]],
    statement_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    directions: set[str] = set()
    for row in rows:
        statement = statement_by_id.get(str(row.get("statement_id") or ""), {})
        if statement.get("concept_leaf") != "legal.proceeding":
            continue
        quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        if re.search(
            r"\bdismiss(?:es|ed|al)?\b.{0,100}\b(?:due to|because of)\b.{0,80}"
            r"\b(?:lack|absence) of\b.{0,40}\b(?:fda|ema)?\s*approval\b|"
            r"\b(?:lack|absence) of\b.{0,40}\b(?:fda|ema)?\s*approval\b"
            r".{0,100}\bdismiss(?:es|ed|al)?\b",
            quote,
            re.I,
        ):
            directions.add(str(row.get("semantic_sentiment") or ""))
    return {"positive", "negative"} <= directions


def _regulatory_challenge_tradeoff(
    rows: Iterable[Mapping[str, Any]],
    statement_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    legal_challenge = False
    active_hold = False
    for row in rows:
        statement = statement_by_id.get(str(row.get("statement_id") or ""), {})
        concept = str(statement.get("concept_leaf") or "")
        quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        if (
            concept == "legal.proceeding"
            and row.get("semantic_sentiment") == "positive"
            and re.search(
                r"\bfil(?:e[sd]?|ing)\b.{0,80}\bcomplaint\b.{0,80}"
                r"\bagainst\b.{0,80}\b(?:fda|ema|regulator|agency)\b",
                quote,
                re.I,
            )
        ):
            legal_challenge = True
        if (
            concept in {"clinical.regulatory_milestone", "regulatory.action"}
            and row.get("semantic_sentiment") == "negative"
            and re.search(r"\bclinical hold\b", quote, re.I)
        ):
            active_hold = True
    return legal_challenge and active_hold


def _clinical_reassessment_tradeoff(
    rows: Iterable[Mapping[str, Any]],
    statement_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    favorable_reassessment = False
    active_hold = False
    for row in rows:
        statement = statement_by_id.get(str(row.get("statement_id") or ""), {})
        concept = str(statement.get("concept_leaf") or "")
        quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        if (
            concept == "clinical.trial_result"
            and row.get("semantic_sentiment") == "positive"
            and re.search(
                r"\bconclud(?:e[sd]?|ing)\b.{0,100}\bnot (?:a )?case of\b"
                r".{0,120}\brevis(?:e[sd]?|ing) (?:the )?diagnosis\b",
                quote,
                re.I,
            )
        ):
            favorable_reassessment = True
        if (
            concept in {"clinical.regulatory_milestone", "regulatory.action"}
            and row.get("semantic_sentiment") == "negative"
            and re.search(r"\bclinical hold\b", quote, re.I)
        ):
            active_hold = True
    return favorable_reassessment and active_hold


def _evidence_package_key(statement: Mapping[str, Any]) -> str:
    concept = str(statement.get("concept_leaf") or "unknown")
    quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
    normalized = re.sub(r"\s+", " ", quote).strip().casefold()

    if concept.startswith("guidance."):
        metrics = _financial_metric_keys(normalized) or ("outlook",)
        horizons = tuple(sorted({
            str(fact.get("horizon") or "").casefold()
            for fact in statement.get("typed_facts", ())
            if fact.get("horizon")
        }))
        if not horizons:
            match = re.search(r"\b(q[1-4](?:\s+20\d{2})?|fy\s*\d{2,4}|full[- ]year)\b", normalized)
            horizons = (match.group(1).replace(" ", ""),) if match else ("unspecified",)
        return "guidance:" + "+".join((*metrics, *horizons))

    if concept.startswith("earnings.") or concept.startswith("financial."):
        metrics = _financial_metric_keys(normalized)
        return "financial:" + "+".join(metrics or (concept.split(".", 1)[1],))

    if concept.startswith("capital."):
        if concept == "capital.financing":
            return "capital:financing"
        if concept in {"capital.deleveraging", "capital.structure"}:
            return "capital:balance_sheet"
        return concept

    if concept == "clinical.regulatory_milestone" or concept.startswith("regulatory."):
        return "medical_regulatory"
    if concept == "product.milestone" and re.search(
        r"\b(?:fda|ema|regulator|approv|authoriz|clearance)\w*\b",
        normalized,
        re.I,
    ):
        return "medical_regulatory"
    if concept.startswith("clinical."):
        return "clinical:" + concept.split(".", 1)[1]
    if concept.startswith("legal."):
        return "legal:" + concept.split(".", 1)[1]
    return concept


def _financial_metric_keys(normalized: str) -> tuple[str, ...]:
    patterns = (
        ("eps", r"\b(?:eps|earnings per share)\b"),
        ("revenue", r"\b(?:revenues?|sales)\b"),
        ("profit", r"\b(?:net income|profit|earnings|net loss|losses?)\b"),
        ("margin", r"\b(?:gross|operating|ebitda|profit) margins?\b"),
        ("cash_flow", r"\b(?:free cash flow|operating cash flow|cash burn)\b"),
        ("ebitda", r"\bebitda\b"),
        ("orders", r"\b(?:orders?|bookings|backlog|demand)\b"),
    )
    return tuple(key for key, pattern in patterns if re.search(pattern, normalized, re.I))


def _benchmarked_result_cue_count(statement: Mapping[str, Any], direction: str) -> int:
    quote = str((statement.get("evidence_spans") or [{}])[0].get("quote", ""))
    if direction == "positive":
        cue_pattern = r"\b(?:beat(?:s|en)?|surpass(?:es|ed|ing)?|above|better[- ]than)\b"
        comparator_pattern = r"\b(?:expectations?|estimates?|consensus|views?)\b"
    else:
        cue_pattern = r"\b(?:miss(?:es|ed)?|below|worse[- ]than|downbeat)\b"
        comparator_pattern = r"\b(?:expectations?|estimates?|consensus|views?)\b"
    comparators = list(re.finditer(comparator_pattern, quote, re.I))
    cue_starts: set[int] = set()
    for cue in re.finditer(cue_pattern, quote, re.I):
        if any(
            max(cue.start() - comparator.end(), comparator.start() - cue.end(), 0)
            <= 100
            for comparator in comparators
        ) or (
            direction == "negative"
            and re.search(
                r"\b(?:earnings|results?)\b.{0,30}$",
                quote[max(0, cue.start() - 40):cue.start()],
                re.I,
            )
        ):
            cue_starts.add(cue.start())
    return len(cue_starts)


def derive_synthesis(
    *,
    entities: Iterable[Mapping[str, Any]],
    statements: Iterable[Mapping[str, Any]],
    participations: Iterable[Mapping[str, Any]],
    issuer_views: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Render a deterministic, evidence-preserving view of certified primitives.

    The readable text is deliberately assembled from exact source quotes. It is
    not an abstractive summary and therefore cannot introduce an unsupported
    claim. Presentation-only compression can be changed without relabeling.
    """
    entity_by_id = {str(row["entity_id"]): row for row in entities}
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    parts_by_entity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in participations:
        parts_by_entity[str(row["entity_id"])].append(row)

    issuer_summaries: list[dict[str, Any]] = []
    for view in issuer_views:
        entity_id = str(view["entity_id"])
        ordered_ids = [
            statement_id
            for statement_id in view["statement_ids"]
            if statement_id in statement_by_id
        ]
        clauses = []
        for statement_id in ordered_ids:
            statement = statement_by_id[statement_id]
            quote = str(statement["evidence_spans"][0]["quote"]).strip()
            clauses.append(f"{statement['concept_leaf']}: {quote}")
        issuer_summaries.append(
            {
                "entity_id": entity_id,
                "display_name": str(entity_by_id.get(entity_id, {}).get("display_name", "")),
                "composite_sentiment": str(view["composite_sentiment"]),
                "statement_ids": ordered_ids,
                "positive_statement_ids": list(view["positive_statement_ids"]),
                "negative_statement_ids": list(view["negative_statement_ids"]),
                "neutral_statement_ids": list(view["neutral_statement_ids"]),
                "readable_summary": " | ".join(clauses),
            }
        )
    return {
        "renderer_version": SYNTHESIS_VERSION,
        "document_statement_ids": list(statement_by_id),
        "issuer_summaries": issuer_summaries,
    }


def derive_eligibility(
    *,
    entities: Iterable[Mapping[str, Any]],
    statements: Iterable[Mapping[str, Any]],
    participations: Iterable[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    quality_flags: Iterable[str],
) -> list[dict[str, Any]]:
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    parts_by_entity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in participations:
        parts_by_entity[str(row["entity_id"])].append(row)
    flags = set(quality_flags)
    purpose = envelope["communication_purpose"]["value"]
    origin = envelope["information_origin"]["value"]
    results: list[dict[str, Any]] = []
    for entity in entities:
        if entity.get("entity_kind") not in {"issuer", "security"}:
            continue
        entity_id = str(entity["entity_id"])
        rows = parts_by_entity.get(entity_id, [])
        substantive = [
            statement_by_id[str(row["statement_id"])]
            for row in rows
            if str(row["statement_id"]) in statement_by_id
            and statement_by_id[str(row["statement_id"])]["statement_kind"] in {"event", "assessment", "forecast", "background"}
            and row.get("semantic_role") != "none"
        ]
        current_event = any(
            (row["statement_kind"] == "event" and row["time_relation"] == "current")
            or (
                row["statement_kind"] == "forecast"
                and row["concept_leaf"] == "guidance.issued"
                and row["time_relation"] == "forward"
            )
            for row in substantive
        )
        has_semantic_implication = any(row.get("semantic_sentiment") in {"positive", "negative"} for row in rows)
        identity_ok = entity.get("identity_status") == "resolved"
        tradable_security = entity.get("entity_kind") == "security" and bool(str(entity.get("ticker", "")).strip())
        evidence_ok = bool(substantive) and not ({"invalid_text", "unrendered_text", "ambiguous_identity", "unresolved_identity"} & flags)
        trigger = tradable_security and identity_ok and evidence_ok and current_event and has_semantic_implication and purpose == "report" and origin != "analyst"
        reaction = trigger and purpose not in {"recap", "explain_move"}
        history = identity_ok and bool(substantive)
        analyst = tradable_security and identity_ok and origin == "analyst" and any(row["statement_kind"] in {"assessment", "forecast"} for row in substantive)
        for product, eligible in (
            ("forecast_trigger", trigger),
            ("reaction_study", reaction),
            ("issuer_history", history),
            ("analyst_evaluation", analyst),
        ):
            reasons = _eligibility_reasons(product, eligible, identity_ok, tradable_security, evidence_ok, current_event, has_semantic_implication, purpose, origin)
            results.append(
                {
                    "entity_id": entity_id,
                    "product": product,
                    "eligible": eligible,
                    "policy_id": f"{POLICY_VERSION}:{product}",
                    "reasons": reasons,
                    "blocking_flags": sorted(flag for flag in flags if flag in {"invalid_text", "unrendered_text", "ambiguous_identity", "unresolved_identity"}),
                }
            )
    return results


def _eligibility_reasons(
    product: str,
    eligible: bool,
    identity_ok: bool,
    tradable_security: bool,
    evidence_ok: bool,
    current_event: bool,
    implication: bool,
    purpose: str,
    origin: str,
) -> list[str]:
    if eligible:
        return [f"eligible_under:{product}"]
    reasons = []
    if not identity_ok:
        reasons.append("identity_not_resolved_as_of_publication")
    if product in {"forecast_trigger", "reaction_study", "analyst_evaluation"} and not tradable_security:
        reasons.append("no_resolved_tradable_security")
    if not evidence_ok:
        reasons.append("insufficient_trustworthy_evidence")
    if product in {"forecast_trigger", "reaction_study"}:
        if not current_event:
            reasons.append("no_current_event")
        if not implication:
            reasons.append("no_positive_or_negative_semantic_implication")
        if purpose != "report":
            reasons.append(f"communication_purpose:{purpose}")
        if origin == "analyst":
            reasons.append("analyst_origin_excluded_from_issuer_event_policy")
    if product == "analyst_evaluation" and origin != "analyst":
        reasons.append("not_analyst_origin")
    return reasons or ["policy_requirements_not_met"]

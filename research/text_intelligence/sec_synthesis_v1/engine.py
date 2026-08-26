from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    CONTRACT_VERSION,
    ENGINE_VERSION,
    PRODUCTION_VERSION,
    REGISTRY_VERSION,
    RENDERER_VERSION,
    validate_document,
)


_DISCLOSURE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (concept, re.compile(pattern, re.I), prior)
    for concept, pattern, prior in (
        ("results.performance", r"\b(?:revenue|net income|net loss|earnings|operating income|gross margin|adjusted ebitda)\b", "contextual"),
        ("guidance.change", r"\b(?:guidance|outlook|forecast|raises?|lowers?|reaffirms?|withdraws?)\b", "contextual"),
        ("liquidity.going_concern", r"\b(?:going concern|substantial doubt|liquidity|cash runway|working capital deficit)\b", "negative"),
        ("accounting.restatement", r"\b(?:restate|restatement|should no longer be relied upon)\b", "negative"),
        ("controls.material_weakness", r"\b(?:material weakness|ineffective internal control|significant deficiency)\b", "negative"),
        ("financing.capital_raise", r"\b(?:public offering|private placement|registered direct|debt financing|credit facility|convertible note)\b", "contextual"),
        ("capital_structure.dilution", r"\b(?:dilution|dilutive|warrant|preferred stock|shares? issuable|at-the-market)\b", "negative"),
        ("ma.transaction", r"\b(?:merger|acquisition|acquire|business combination|asset sale|divestiture)\b", "contextual"),
        ("commercial.contract", r"\b(?:material definitive agreement|purchase agreement|supply agreement|commercial contract|purchase order)\b", "contextual"),
        ("legal.regulatory", r"\b(?:litigation|lawsuit|settlement|investigation|subpoena|enforcement|regulatory action)\b", "negative"),
        ("operations.restructuring", r"\b(?:restructuring|workforce reduction|layoff|facility closure|discontinued operations)\b", "negative"),
        ("governance.change", r"\b(?:(?:chief executive officer|chief financial officer|director).{0,100}(?:resign(?:ed|ation)?|appoint(?:ed|ment)?|depart(?:ed|ure)?|successor|transition)|(?:resign(?:ed|ation)?|appoint(?:ed|ment)?|depart(?:ed|ure)?|successor|transition).{0,100}(?:chief executive officer|chief financial officer|director)|management change)\b", "contextual"),
        ("cybersecurity.incident", r"\b(?:cybersecurity incident|cyber attack|data breach|ransomware)\b", "negative"),
        ("capital_return", r"\b(?:dividend|share repurchase|stock buyback)\b", "positive"),
    )
)

_POSITIVE = re.compile(r"\b(?:increase[ds]?|improv(?:e|ed|ement)|strong(?:er)?|record|profit(?:able)?|approval|cleared|resolved|favorable|raised|reaffirmed|exceed(?:ed)?|growth|expanded)\b", re.I)
_NEGATIVE = re.compile(r"\b(?:decrease[ds]?|declin(?:e|ed)|weak(?:er|ness)?|loss|adverse|default|breach|impairment|terminated|withdrawn|lowered|miss(?:ed)?|shortfall|doubt|investigation|dilution)\b", re.I)
_FORWARD = re.compile(r"\b(?:expects?|guidance|outlook|forecast|will|plans?|intends?)\b", re.I)
_HISTORICAL = re.compile(r"\b(?:previously|historically|in prior years?|during the year ended)\b", re.I)
_CONDITIONAL = re.compile(r"\b(?:may|might|could|subject to|if|contingent)\b", re.I)

_CONCEPT_FAMILIES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("revenue", ("Revenue", "Revenues", "SalesRevenue", "RevenueFromContractWithCustomer"), "higher_is_stronger", "duration"),
    ("gross_profit", ("GrossProfit",), "higher_is_stronger", "duration"),
    ("operating_income", ("OperatingIncomeLoss", "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"), "higher_is_stronger", "duration"),
    ("net_income", ("NetIncomeLoss", "ProfitLoss"), "higher_is_stronger", "duration"),
    ("operating_cash_flow", ("NetCashProvidedByUsedInOperatingActivities",), "higher_is_stronger", "duration"),
    ("cash", ("CashAndCashEquivalents", "CashCashEquivalentsRestrictedCash"), "higher_is_stronger", "instant"),
    ("assets", ("Assets",), "contextual", "instant"),
    ("current_assets", ("AssetsCurrent",), "higher_is_stronger", "instant"),
    ("current_liabilities", ("LiabilitiesCurrent",), "lower_is_stronger", "instant"),
    ("debt", ("LongTermDebt", "DebtCurrent", "LongTermDebtCurrent", "LongTermDebtNoncurrent"), "lower_is_stronger", "instant"),
    ("equity", ("StockholdersEquity", "Equity"), "higher_is_stronger", "instant"),
    ("shares", ("EntityCommonStockSharesOutstanding", "WeightedAverageNumberOfSharesOutstanding"), "lower_is_stronger", "instant"),
    ("inventory", ("InventoryNet",), "contextual", "instant"),
    ("accounts_receivable", ("AccountsReceivableNet",), "contextual", "instant"),
)


class SecSynthesisEngine:
    """Compile one filing accession into evidence-preserving SEC synthesis."""

    def process(
        self,
        *,
        filing: Mapping[str, Any],
        documents: Sequence[Mapping[str, Any]],
        facts: Sequence[Mapping[str, Any]] = (),
        source_hash: str,
    ) -> dict[str, Any]:
        accession = str(filing.get("accession_number") or filing.get("source_id") or "")
        cik = str(filing.get("cik") or "")
        accepted = str(filing.get("accepted_at_utc") or filing.get("source_timestamp") or "")
        envelope = self._envelope(filing, documents)
        entities = self._entities(filing, documents)
        disclosures = self._disclosures(accession, documents)
        transitions = self._transitions(accession, facts)
        reconciliation = self._reconcile(disclosures, transitions)
        views = self._issuer_views(entities, disclosures, transitions)
        synthesis = self._synthesis(envelope, views, disclosures, transitions, reconciliation)
        quality_flags = self._quality_flags(envelope, disclosures, transitions)
        eligibility = self._eligibility(entities, disclosures, transitions, quality_flags)
        document = {
            "contract_version": CONTRACT_VERSION,
            "concept_registry_version": REGISTRY_VERSION,
            "accession_number": accession,
            "cik": cik,
            "accepted_at_utc": accepted,
            "source_hash": source_hash,
            "filing_envelope": envelope,
            "entities": entities,
            "narrative_disclosures": disclosures,
            "fundamental_transitions": transitions,
            "reconciliation": reconciliation,
            "issuer_views": views,
            "synthesis": synthesis,
            "eligibility": eligibility,
            "quality_flags": quality_flags,
            "production": {
                "production_version": PRODUCTION_VERSION,
                "engine_version": ENGINE_VERSION,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "source_revision": source_hash,
            },
        }
        errors = validate_document(document)
        if errors:
            raise ValueError("invalid SEC Synthesis document: " + "; ".join(errors))
        return document

    @staticmethod
    def _envelope(filing: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        form = str(filing.get("form_type") or "").upper()
        filing_items_value = filing.get("filing_items") or filing.get("items") or ()
        filing_items = (
            [item.strip() for item in str(filing_items_value).split(",") if item.strip()]
            if isinstance(filing_items_value, str)
            else list(filing_items_value)
        )
        document_rows = []
        for row in documents:
            document_rows.append({
                "document_id": str(row.get("document_id") or ""),
                "document_type": str(row.get("document_type") or ""),
                "document_role": str(row.get("document_role") or ""),
                "description": str(row.get("description") or ""),
                "text_sha256": str(row.get("text_sha256") or row.get("source_text_sha256") or ""),
                "text_chars": len(str(row.get("text") or "")),
            })
        return {
            "company_name": str(filing.get("company_name") or ""),
            "form_type": form,
            "amendment": form.endswith("/A"),
            "filing_date": str(filing.get("filing_date") or ""),
            "report_date": str(filing.get("report_date") or ""),
            "accepted_at_source": str(filing.get("accepted_at_source") or ""),
            "filing_items": filing_items,
            "documents": document_rows,
            "document_count": len(document_rows),
            "narrative_document_count": sum(bool(str(row.get("text") or "").strip()) for row in documents),
        }

    @staticmethod
    def _entities(filing: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        ticker = next((str(row.get("ticker") or "").upper() for row in documents if row.get("ticker")), "")
        return [{
            "entity_id": f"issuer:cik:{str(filing.get('cik') or '')}",
            "cik": str(filing.get("cik") or ""),
            "display_name": str(filing.get("company_name") or ""),
            "ticker": ticker,
            "role": "primary_filer",
            "identity_status": "resolved" if ticker else "issuer_resolved_security_unmapped",
        }]

    @staticmethod
    def _disclosures(accession: str, documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for document in documents:
            text = str(document.get("text") or "")
            if not text.strip():
                continue
            document_id = str(document.get("document_id") or accession)
            for start, end, quote in _semantic_segments(text):
                concepts = [
                    (concept, prior)
                    for concept, pattern, prior in _DISCLOSURE_RULES
                    if pattern.search(quote) and not _is_boilerplate(concept, quote)
                ]
                for concept, prior in concepts:
                    key = (document_id, concept, quote.casefold())
                    if key in seen:
                        continue
                    seen.add(key)
                    positive = len(_POSITIVE.findall(quote))
                    negative = len(_NEGATIVE.findall(quote))
                    if prior == "positive":
                        positive += 1
                    elif prior == "negative":
                        negative += 1
                    direction = _direction(positive, negative)
                    digest = hashlib.sha256(f"{document_id}|{concept}|{start}|{end}".encode()).hexdigest()[:16]
                    output.append({
                        "disclosure_id": f"{accession}:disclosure:{digest}",
                        "document_id": document_id,
                        "document_role": str(document.get("document_role") or ""),
                        "concept": concept,
                        "title": _compact(quote, 160),
                        "economic_direction": direction,
                        "positive_strength": min(4, positive),
                        "negative_strength": min(4, negative),
                        "epistemic_status": "conditional" if _CONDITIONAL.search(quote) else "expected" if _FORWARD.search(quote) else "confirmed",
                        "time_relation": "historical" if _HISTORICAL.search(quote) else "forward" if _FORWARD.search(quote) else "current",
                        "evidence": [{
                            "evidence_id": f"{accession}:evidence:{digest}",
                            "document_id": document_id,
                            "source_field": "rendered_text",
                            "start": start,
                            "end": end,
                            "quote": quote,
                            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                        }],
                    })
        return output

    @staticmethod
    def _transitions(accession: str, facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        current = [row for row in facts if str(row.get("accession_number") or "") == accession]
        by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in facts:
            family = _concept_family(str(row.get("tag") or ""))
            if family:
                by_family[family[0]].append(row)
        output: list[dict[str, Any]] = []
        for row in current:
            family = _concept_family(str(row.get("tag") or ""))
            if not family:
                continue
            family_name, direction_policy, period_kind = family
            prior_candidates = [
                item for item in by_family[family_name]
                if str(item.get("accession_number") or "") != accession
                and str(item.get("unit_code") or "") == str(row.get("unit_code") or "")
                and str(item.get("period_end_date") or "") <= str(row.get("period_end_date") or "")
            ]
            prior_candidates.sort(key=lambda item: (str(item.get("period_end_date") or ""), str(item.get("filed_at_utc") or "")), reverse=True)
            prior = prior_candidates[0] if prior_candidates else None
            comparability = _comparability(row, prior, period_kind)
            current_value = _number(row.get("value"))
            prior_value = _number(prior.get("value")) if prior else None
            absolute = current_value - prior_value if current_value is not None and prior_value is not None else None
            percent = absolute / abs(prior_value) * 100 if absolute is not None and prior_value not in {None, 0} else None
            economic_direction = _transition_direction(percent, direction_policy, comparability)
            digest = hashlib.sha256(f"{row.get('company_fact_id')}|{prior.get('company_fact_id') if prior else ''}".encode()).hexdigest()[:16]
            output.append({
                "transition_id": f"{accession}:transition:{digest}",
                "concept_family": family_name,
                "tag": str(row.get("tag") or ""),
                "taxonomy": str(row.get("taxonomy") or ""),
                "unit_code": str(row.get("unit_code") or ""),
                "period_kind": period_kind,
                "current_fact_id": str(row.get("company_fact_id") or ""),
                "prior_fact_id": str(prior.get("company_fact_id") or "") if prior else "",
                "current_accession": accession,
                "prior_accession": str(prior.get("accession_number") or "") if prior else "",
                "current_period_end": str(row.get("period_end_date") or ""),
                "prior_period_end": str(prior.get("period_end_date") or "") if prior else "",
                "current_value": current_value,
                "prior_value": prior_value,
                "absolute_change": absolute,
                "percent_change": percent,
                "comparability": comparability,
                "economic_direction": economic_direction,
                "materiality": _materiality(percent),
                "availability_at": str(row.get("accepted_at_utc") or row.get("filed_at_utc") or row.get("recorded_at_utc") or ""),
            })
        return output

    @staticmethod
    def _reconcile(disclosures: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for family in sorted({_reconciliation_family(row.get("concept")) for row in disclosures} | {str(row.get("concept_family") or "") for row in transitions} - {""}):
            disclosure_ids = [str(row["disclosure_id"]) for row in disclosures if _reconciliation_family(row.get("concept")) == family]
            transition_ids = [str(row["transition_id"]) for row in transitions if row.get("concept_family") == family]
            directions = {
                str(row.get("economic_direction") or "neutral")
                for row in [*disclosures, *transitions]
                if (_reconciliation_family(row.get("concept")) if "concept" in row else row.get("concept_family")) == family
                and row.get("economic_direction") not in {"neutral", "contextual", "unresolved"}
            }
            state = "contradiction" if {"positive", "negative"} <= directions else "independent_confirmation" if disclosure_ids and transition_ids else "narrative_only" if disclosure_ids else "xbrl_only"
            output.append({
                "reconciliation_id": f"reconcile:{family}",
                "concept_family": family,
                "state": state,
                "narrative_disclosure_ids": disclosure_ids,
                "transition_ids": transition_ids,
            })
        return output

    @staticmethod
    def _issuer_views(entities: Sequence[Mapping[str, Any]], disclosures: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        positive = max([int(row.get("positive_strength") or 0) for row in disclosures] + [1 if row.get("economic_direction") == "positive" else 0 for row in transitions] + [0])
        negative = max([int(row.get("negative_strength") or 0) for row in disclosures] + [1 if row.get("economic_direction") == "negative" else 0 for row in transitions] + [0])
        direction = _direction(positive, negative)
        return [{
            "entity_id": str(entity.get("entity_id") or ""),
            "cik": str(entity.get("cik") or ""),
            "ticker": str(entity.get("ticker") or ""),
            "composite_sentiment": direction,
            "positive_strength": positive,
            "negative_strength": negative,
            "disclosure_ids": [str(row["disclosure_id"]) for row in disclosures],
            "transition_ids": [str(row["transition_id"]) for row in transitions],
        } for entity in entities]

    @staticmethod
    def _synthesis(envelope: Mapping[str, Any], views: Sequence[Mapping[str, Any]], disclosures: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]], reconciliation: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        company = str(envelope.get("company_name") or "Issuer")
        form = str(envelope.get("form_type") or "filing")
        direction = str(views[0].get("composite_sentiment") or "neutral") if views else "neutral"
        material = sorted(disclosures, key=lambda row: max(int(row.get("positive_strength") or 0), int(row.get("negative_strength") or 0)), reverse=True)
        comparable = [row for row in transitions if row.get("comparability") == "comparable"]
        summary_parts = [f"{company} filed {form} with {len(disclosures)} material narrative disclosures"]
        if transitions:
            summary_parts.append(f"{len(transitions)} XBRL transitions, {len(comparable)} comparable")
        summary_parts.append(f"overall economic implication is {direction}")
        return {
            "renderer_version": RENDERER_VERSION,
            "headline": f"{form} · {company}",
            "readable_summary": "; ".join(summary_parts) + ".",
            "composite_sentiment": direction,
            "highlights": [_compact(str(row.get("title") or ""), 220) for row in material if row.get("economic_direction") == "positive"][:4],
            "risks": [_compact(str(row.get("title") or ""), 220) for row in material if row.get("economic_direction") == "negative"][:4],
            "mixed_or_contextual": [_compact(str(row.get("title") or ""), 220) for row in material if row.get("economic_direction") in {"mixed", "neutral", "contextual"}][:4],
            "reconciliation_conflicts": [str(row["reconciliation_id"]) for row in reconciliation if row.get("state") == "contradiction"],
        }

    @staticmethod
    def _quality_flags(envelope: Mapping[str, Any], disclosures: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]]) -> list[str]:
        flags = []
        if not envelope.get("narrative_document_count"):
            flags.append("narrative_text_unavailable")
        if not disclosures:
            flags.append("no_material_narrative_disclosure_detected")
        if not transitions:
            flags.append("xbrl_transitions_unavailable")
        if any(row.get("comparability") != "comparable" for row in transitions):
            flags.append("limited_xbrl_comparability")
        return flags

    @staticmethod
    def _eligibility(entities: Sequence[Mapping[str, Any]], disclosures: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]], quality_flags: Sequence[str]) -> list[dict[str, Any]]:
        entity_id = str(entities[0].get("entity_id") or "") if entities else ""
        resolved = bool(entities and entities[0].get("ticker"))
        current_disclosures = [row for row in disclosures if row.get("time_relation") != "historical"]
        comparable = [row for row in transitions if row.get("comparability") == "comparable"]
        forecast_evidence = bool(current_disclosures or comparable)
        products = {
            "issuer_history": bool(disclosures or transitions),
            "risk_change": any(row.get("economic_direction") == "negative" for row in disclosures) or any(row.get("economic_direction") == "negative" for row in comparable),
            "guidance_change": any(row.get("concept") == "guidance.change" for row in current_disclosures),
            "fundamental_transition_context": bool(comparable),
            "reaction_study": resolved and bool(current_disclosures or comparable),
            "forecast_trigger": resolved and forecast_evidence,
        }
        output = []
        for product, eligible in products.items():
            blockers = []
            if product in {"reaction_study", "forecast_trigger"} and not resolved:
                blockers.append("security_identity_unresolved")
            if product == "forecast_trigger" and not forecast_evidence:
                blockers.append("no_current_material_forecast_evidence")
            if product == "fundamental_transition_context" and not comparable:
                blockers.append("no_comparable_xbrl_transition")
            output.append({
                "entity_id": entity_id,
                "product": product,
                "eligible": bool(eligible),
                "policy_id": "sec_synthesis_eligibility_v1",
                "reasons": (
                    [
                        reason
                        for reason, present in (
                            ("current_material_narrative_disclosure", bool(current_disclosures)),
                            ("comparable_xbrl_transition", bool(comparable)),
                        )
                        if present
                    ]
                    if eligible and product in {"forecast_trigger", "reaction_study"}
                    else ["current_material_filing_evidence"] if eligible else []
                ),
                "blocking_flags": blockers,
            })
        return output


def _semantic_segments(text: str) -> Iterable[tuple[int, int, str]]:
    for match in re.finditer(r"[^\n]{20,1600}(?:\n+|$)", text):
        raw = match.group(0)
        for sentence in re.finditer(r"[^.!?\n]{20,800}(?:[.!?](?=\s|$)|$)", raw):
            source = sentence.group(0)
            leading = len(source) - len(source.lstrip())
            trailing = len(source) - len(source.rstrip())
            start = match.start() + sentence.start() + leading
            end = match.start() + sentence.end() - trailing
            quote = text[start:end]
            if len(quote) >= 20:
                yield start, end, quote


def _is_boilerplate(concept: str, quote: str) -> bool:
    normalized = " ".join(quote.casefold().split())
    if concept == "legal.regulatory" and (
        "private securities litigation reform act" in normalized
        or ("forward-looking statement" in normalized and "litigation" in normalized)
    ):
        return True
    if concept == "governance.change" and re.search(
        r"\b(?:by:|signature|title:)\b", normalized
    ) and not re.search(
        r"\b(?:resign|appoint|depart|successor|transition|management change)\b",
        normalized,
    ):
        return True
    return False


def _concept_family(tag: str) -> tuple[str, str, str] | None:
    normalized = re.sub(r"[^a-z]", "", tag.casefold())
    for family, aliases, direction, period_kind in _CONCEPT_FAMILIES:
        if any(re.sub(r"[^a-z]", "", alias.casefold()) in normalized for alias in aliases):
            return family, direction, period_kind
    return None


def _comparability(current: Mapping[str, Any], prior: Mapping[str, Any] | None, period_kind: str) -> str:
    if prior is None:
        return "missing_prior"
    if str(current.get("unit_code") or "") != str(prior.get("unit_code") or ""):
        return "unit_mismatch"
    current_period = str(current.get("fiscal_period") or "").upper()
    prior_period = str(prior.get("fiscal_period") or "").upper()
    if period_kind == "duration" and current_period != "FY":
        return "insufficient_duration_context"
    if current_period and prior_period and current_period != prior_period:
        return "fiscal_period_mismatch"
    return "comparable"


def _transition_direction(change: float | None, policy: str, comparability: str) -> str:
    if comparability != "comparable" or change is None or abs(change) < 0.05:
        return "unresolved" if comparability != "comparable" else "neutral"
    if policy == "contextual":
        return "contextual"
    favorable = change > 0 if policy == "higher_is_stronger" else change < 0
    return "positive" if favorable else "negative"


def _materiality(change: float | None) -> str:
    if change is None:
        return "unknown"
    magnitude = abs(change)
    return "high" if magnitude >= 20 else "medium" if magnitude >= 5 else "low"


def _direction(positive: int, negative: int) -> str:
    if positive and negative:
        if positive >= negative + 2:
            return "positive"
        if negative >= positive + 2:
            return "negative"
        return "mixed"
    return "positive" if positive else "negative" if negative else "neutral"


def _reconciliation_family(value: Any) -> str:
    concept = str(value or "")
    if concept.startswith("results"):
        return "revenue"
    if concept.startswith("capital_structure"):
        return "shares"
    if concept.startswith("liquidity"):
        return "cash"
    return concept.split(".", 1)[0]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import CONTRACT_VERSION, PRODUCTION_VERSION, validate_document
from .facts import extract_typed_facts
from .synthesis import derive_eligibility, derive_issuer_views, derive_synthesis


ENGINE_VERSION = "news_synthesis_engine_v1"
EXCHANGE_TICKER_RE = re.compile(r"\b(?:NASDAQ|NYSE|NYSE\s+AMERICAN|NYSEAMERICAN|AMEX|OTC(?:QX|QB)?|TSX|TSXV|CSE)\s*[:\-]\s*([A-Z][A-Z0-9.\-]{0,9})\b", re.I)
CASHTAG_RE = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9.\-]{0,9})\b")
ROUNDUP_RE = re.compile(r"\b(?:stocks?|companies|biggest movers?|gainers?|losers?)\s+(?:moving|to watch)|\bmarket\s+(?:wrap|recap|update)\b", re.I)
WHY_MOVING_RE = re.compile(r"\bwhy\s+(?:is|are|did)\b.*\b(?:stock|shares?)\b.*\bmov", re.I)
ANALYST_RE = re.compile(r"\b(?:analyst|price target|rating|upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?)\b", re.I)
REGULATORY_RE = re.compile(r"\b(?:SEC|FDA|regulator|regulatory|trading halt|compliance notice|Nasdaq (?:comments|halts|notifies|determines)|NYSE (?:comments|halts|notifies|determines))\b", re.I)
AUTOMATED_RE = re.compile(r"\b(?:automated market statistics|benzinga pro data|generated automatically)\b", re.I)


@dataclass(frozen=True, slots=True)
class IssuerIdentity:
    ticker: str
    issuer_id: str
    display_name: str
    aliases: tuple[str, ...]
    exchange_code: str = ""
    security_id: str = ""
    list_date: date | None = None
    delisted_date: date | None = None

    def valid_on(self, day: date | None) -> bool:
        return not day or ((not self.list_date or day >= self.list_date) and (not self.delisted_date or day <= self.delisted_date))


class IssuerIdentityIndex:
    """Point-in-time identity authority; provider tickers are candidates only."""

    def __init__(self, identities: Iterable[IssuerIdentity]) -> None:
        self._by_ticker: dict[str, list[IssuerIdentity]] = {}
        self._by_alias: dict[str, list[IssuerIdentity]] = {}
        for row in identities:
            ticker = row.ticker.upper().strip()
            if not ticker:
                continue
            self._by_ticker.setdefault(ticker, []).append(row)
            for alias in row.aliases:
                key = _normalize_alias(alias)
                if _safe_alias(key):
                    self._by_alias.setdefault(key, []).append(row)

    def resolve(self, *, text: str, candidates: Sequence[str], timestamp: str) -> list[dict[str, Any]]:
        day = _as_date(timestamp)
        explicit = {value.upper() for value in EXCHANGE_TICKER_RE.findall(text)} | {value.upper() for value in CASHTAG_RE.findall(text)}
        candidate_set = {str(value).upper().strip() for value in candidates if value}
        matches: dict[str, set[str]] = {ticker: {"explicit_ticker_in_text"} for ticker in explicit}
        normalized_text = f" {_normalize_alias(text)} "
        for alias, rows in self._by_alias.items():
            if f" {alias} " not in normalized_text:
                continue
            valid = [row for row in rows if row.valid_on(day)]
            tickers = {row.ticker for row in valid}
            preferred = tickers & candidate_set
            if len(preferred) == 1:
                tickers = preferred
            if len(tickers) == 1:
                matches.setdefault(next(iter(tickers)), set()).add(f"issuer_alias:{alias}")
        for ticker in candidate_set & set(matches):
            matches[ticker].add("provider_candidate_supported")
        entities: list[dict[str, Any]] = []
        for ticker, evidence in sorted(matches.items()):
            known = list(self._by_ticker.get(ticker, ()))
            valid = [row for row in known if row.valid_on(day)]
            identity = valid[0] if len({row.issuer_id for row in valid}) == 1 else None
            entities.append({
                "entity_id": (identity.security_id or f"security:{identity.issuer_id}:{ticker}") if identity else f"security:{ticker}:{timestamp[:10]}",
                "entity_kind": "security",
                "display_name": identity.display_name if identity else ticker,
                "ticker": ticker,
                "identity_status": "resolved" if identity else ("ambiguous" if valid else "not_tradable_as_of" if known else "unresolved"),
                "identity_evidence": sorted(evidence),
            })
        return entities


@dataclass(frozen=True, slots=True)
class ConceptRule:
    concept: str
    pattern: re.Pattern[str]
    statement_kind: str
    positive: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()


def _rule(concept: str, terms: str, kind: str = "event", *, positive: Sequence[str] = (), negative: Sequence[str] = ()) -> ConceptRule:
    return ConceptRule(concept, re.compile(terms, re.I), kind, tuple(positive), tuple(negative))


RULES = (
    _rule("analyst.rating_action", r"\b(?:upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?|rates?|rating)\b(?:.{0,100})\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|rating)\b|\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral)\s+rating\b", "assessment", positive=("upgrade", "buy", "outperform", "overweight"), negative=("downgrade", "sell", "underperform", "underweight")),
    _rule("analyst.price_target_action", r"\b(?:price target|target price)\b", "forecast", positive=("raises", "raised", "higher"), negative=("cuts", "cut", "lowers", "lowered")),
    _rule("earnings.performance", r"\b(?:earnings|EPS|revenue|sales|net income|profit|quarterly results?)\b.{0,180}\b(?:reports?|reported|beat[sd]?|miss(?:es|ed)?|rose|fell|declin(?:e|ed)|grew|increase[sd]?|decrease[sd]?|loss)\b|\b(?:reports?|reported|beat[sd]?|miss(?:es|ed)?|rose|fell|grew)\b.{0,100}\b(?:earnings|EPS|revenue|sales|profit)\b", positive=("beat", "grew", "rose", "record", "increase"), negative=("miss", "fell", "decline", "decrease", "loss")),
    _rule("guidance.issued", r"\b(?:issues?|provides?|raises?|lowers?|cuts?|reaffirms?|withdraws?|updates?)\b.{0,80}\b(?:guidance|outlook|forecast)\b|\b(?:guidance|outlook)\b.{0,80}\b(?:raised|lowered|cut|reaffirmed|withdrawn|expects?)\b", "forecast", positive=("raise", "increas", "reaffirm"), negative=("cut", "lower", "withdraw", "reduce")),
    _rule("corporate_transaction.acquisition", r"\b(?:acquir(?:e|es|ed|ing)|acquisition|merger|takeover)\b", positive=("agreed", "complete", "closes", "approved"), negative=("terminate", "withdraw", "no longer pursue", "blocked")),
    _rule("corporate_transaction.asset_sale", r"\b(?:asset sale|divest(?:s|ed|iture)|sell(?:s|ing)? its .*business)\b", positive=("complete", "proceeds"), negative=("distress",)),
    _rule("capital.financing", r"\b(?:offering|private placement|at-the-market|ATM program|financing|convertible notes?)\b", negative=("dilution", "offering", "placement")),
    _rule("capital.return", r"\b(?:share repurchase|buyback|dividend)\b", positive=("restart", "increase", "raises", "special dividend"), negative=("suspend", "cut", "reduce")),
    _rule("regulatory.action", r"\b(?:trading halt|halted|SEC action|regulatory action|compliance notice)\b", negative=("halt", "suspend", "noncompliance")),
    _rule("clinical.regulatory_milestone", r"\b(?:FDA|EMA)\b.*\b(?:approv|reject|complete response|clinical hold|clearance)\w*\b", positive=("approve", "approval", "clearance"), negative=("reject", "complete response", "hold")),
    _rule("clinical.trial_result", r"\b(?:clinical trial|study|Phase [123])\b.*\b(?:endpoint|results?|data|efficacy|safety)\b", positive=("met", "positive", "improved"), negative=("failed", "missed", "adverse")),
    _rule("legal.proceeding", r"\b(?:lawsuit|litigation|investigation|subpoena|settlement)\b", negative=("lawsuit", "investigation", "subpoena")),
    _rule("listing.market_structure", r"\b(?:reverse split|stock split|delisting|listing compliance|minimum bid|IPO)\b", positive=("regained compliance", "approved listing"), negative=("delisting", "noncompliance", "reverse split")),
    _rule("commercial.contract", r"\b(?:contract|order|award|backlog)\b", positive=("awarded", "wins", "received"), negative=("cancel", "terminate")),
    _rule("product.milestone", r"\b(?:launch|recall|discontinue|product delay)\b", positive=("launch", "approval"), negative=("recall", "delay", "discontinue")),
    _rule("governance.management_change", r"\b(?:appoints?|names?|elects?|resigns?|retires?|steps down|terminates?|replaces?)\b.{0,100}\b(?:chief executive|chief financial|CEO|CFO|president|director|board)\b|\b(?:chief executive|chief financial|CEO|CFO|president|director)\b.{0,80}\b(?:resigns?|retires?|steps down|appointed|named|terminated|replaced)\b", negative=("resign", "terminated", "steps down")),
    _rule("operations.business_update", r"\b(?:business update|restructur|layoff|shutdown|expansion)\w*\b", positive=("expansion", "growth"), negative=("layoff", "shutdown", "restructur")),
    _rule("earnings.release_schedule", r"\b(?:will report|scheduled to report|earnings (?:date|call|release))\b", "reference"),
    _rule("earnings.restatement", r"\b(?:restate|restatement|should no longer be relied upon)\b", negative=("restate", "no longer be relied")),
    _rule("capital.deleveraging", r"\b(?:deleverag|debt repayment|repay(?:s|ed)? .*debt|reduce(?:s|d)? .*debt)\w*\b", positive=("deleverag", "repay", "reduce")),
    _rule("capital.structure", r"\b(?:authorized shares|outstanding shares|share consolidation|capital structure)\b"),
    _rule("credit.solvency", r"\b(?:bankrupt|chapter 11|default|going concern|insolven|liquidity crisis)\w*\b", negative=("bankrupt", "default", "going concern", "insolven", "crisis")),
    _rule("financial.margin", r"\b(?:gross|operating|EBITDA|profit) margins?\b", positive=("expand", "improv", "increase", "accretive"), negative=("contract", "compress", "declin", "dilutive", "difficult", "struggle")),
    _rule("financial.operating_performance", r"\b(?:operating income|operating loss|EBITDA|profitability|net income|net loss|operating profit|results? of operations)\b", positive=("income", "profitab", "improv", "increase"), negative=("loss", "declin", "deterior", "decrease")),
    _rule("financial.cash_flow", r"\b(?:free cash flow|operating cash flow|cash burn)\b", positive=("positive", "increase", "improv"), negative=("negative", "burn", "declin")),
    _rule("financial.liquidity", r"\b(?:cash runway|liquidity|cash and equivalents|working capital)\b", positive=("strong", "sufficient", "improv"), negative=("shortfall", "insufficient", "weak")),
    _rule("financial.loss_exposure", r"\b(?:impairment|write[- ]?down|charge|loss exposure)\b", negative=("impairment", "write", "charge", "loss")),
    _rule("financial.internal_control", r"\b(?:material weakness|internal controls?|control deficiency)\b", negative=("weakness", "deficiency", "ineffective")),
    _rule("financial.credit_quality", r"\b(?:credit rating|credit quality|rating agency)\b", positive=("upgrade", "improv"), negative=("downgrade", "deterior")),
    _rule("estimate.revision", r"\b(?:estimate|consensus)\b.*\b(?:raised|lowered|revised|cut)\b", "forecast", positive=("raised", "higher"), negative=("lowered", "cut")),
    _rule("ownership.position_change", r"\b(?:stake|position|ownership)\b.*\b(?:increased|decreased|sold|bought|acquired)\b", positive=("increased", "bought", "acquired"), negative=("decreased", "sold")),
    _rule("ownership.position", r"\b(?:owns?|ownership|stake|beneficial owner)\b", "background"),
    _rule("commercial.partnership", r"\b(?:partnership|collaboration|strategic alliance|joint venture)\b", positive=("partnership", "collaboration", "alliance")),
    _rule("commercial.demand_condition", r"\b(?:demand|bookings|orders?|backlog)\b", positive=("strong", "increase", "record"), negative=("weak", "declin", "slow")),
    _rule("commercial.competitive_position", r"\b(?:market share|competitive position|competition|competitor)\b", positive=("gain", "leading", "advantage"), negative=("lose", "pressure", "challeng")),
    _rule("operations.workforce", r"\b(?:layoffs?|job cuts?|workforce reduction|hires?|headcount)\b", negative=("layoff", "cut", "reduction")),
    _rule("operations.capacity_change", r"\b(?:capacity|facility|plant|factory)\b.*\b(?:expand|open|close|shutdown|increase|reduce)\w*\b", positive=("expand", "open", "increase"), negative=("close", "shutdown", "reduce")),
    _rule("governance.auditor_change", r"\b(?:auditor|accounting firm)\b.*\b(?:resign|dismiss|appoint|replace)\w*\b", negative=("resign", "dismiss")),
    _rule("governance.shareholder_vote", r"\b(?:shareholder|stockholder)\b.*\b(?:vote|meeting|proposal)\b"),
    _rule("index.membership", r"\b(?:added to|removed from|join(?:s|ed)?|delete(?:d)?)\b.*\b(?:index|S&P|Russell|Nasdaq-100)\b", positive=("added", "join"), negative=("removed", "delete")),
    _rule("technology.cybersecurity_incident", r"\b(?:cyberattack|data breach|ransomware|security incident)\b", negative=("attack", "breach", "ransomware", "incident")),
    _rule("market.options_activity", r"\b(?:options activity|call volume|put volume|unusual options)\b", "market_observation"),
    _rule("market.short_interest_observed", r"\b(?:short interest|short volume|days to cover)\b", "market_observation"),
    _rule("market.technical_analysis", r"\b(?:support|resistance|moving average|RSI|MACD|technical analysis)\b", "assessment"),
    _rule("macro.inflation", r"\b(?:inflation|consumer price index|CPI|producer price index|PPI)\b", "background"),
    _rule("macro.employment", r"\b(?:employment|unemployment|nonfarm payrolls?|jobless claims)\b", "background"),
    _rule("macro.economic_outlook", r"\b(?:economic outlook|recession|economic expansion)\b", "forecast"),
    _rule("financial.interest_rate", r"\b(?:interest rates?|rate hike|rate cut|federal funds rate)\b", "background"),
    _rule("market.price_move_observed", r"\b(?:shares?|stock|equity|index|bitcoin|BTC)\b.{0,100}\b(?:rose|fell|gained|dropped|surged|slid|jumped|rallied|declined|trading (?:up|down)|moved (?:above|below)|higher|lower)\b|\b(?:rose|fell|gained|dropped|surged|slid|jumped|rallied|declined|traded (?:up|down))\b.{0,100}\b(?:shares?|stock|equity|index)\b", "market_observation", positive=("rose", "gained", "surged", "jumped", "rallied", "higher", "trading up", "moved above"), negative=("fell", "dropped", "slid", "declined", "lower", "trading down", "moved below")),
    _rule("market.volume_move_observed", r"\b(?:trading volume|volume spike|unusual volume)\b", "market_observation"),
    _rule("market.trading_status", r"\b(?:halted|trading halt|resumed trading)\b", "market_observation"),
    _rule("market.money_flow_observed", r"\b(?:money flows?|fund flows?|inflows?|outflows?|buying pressure|selling pressure)\b", "market_observation", positive=("positive", "inflow", "buying"), negative=("negative", "outflow", "selling")),
    _rule("analyst.issuer_assessment", r"\b(?:analyst|brokerage|research firm|investment firm)\b.{0,160}\b(?:believes?|expects?|sees?|views?|said|positive|negative|bullish|bearish)\b", "assessment", positive=("positive", "bullish", "upside", "strong"), negative=("negative", "bearish", "downside", "weak")),
    _rule("strategy.valuation_assessment", r"\b(?:valuation|valued|multiple|price[- ]to[- ]earnings|P/E|undervalued|overvalued|cheap|expensive)\b", "assessment", positive=("undervalued", "cheap", "attractive"), negative=("overvalued", "expensive", "premium")),
    _rule("operations.cost_efficiency", r"\b(?:cost savings?|cost reduction|reduce(?:s|d)? costs?|expense reduction|efficiency program|productivity initiative)\b", positive=("savings", "reduction", "efficiency", "productivity"), negative=("higher costs", "cost pressure")),
    _rule("macro.policy_outlook", r"\b(?:central bank|Federal Reserve|Fed|government|policy makers?)\b.{0,160}\b(?:policy|stimulus|rate cuts?|rate hikes?|tighten|ease|intervention)\b|\b(?:monetary|fiscal) policy\b", "forecast"),
    _rule("commodity.inventory", r"\b(?:crude oil|oil|natural gas|gasoline) inventor(?:y|ies)\b|\binventor(?:y|ies)\b.{0,80}\b(?:barrels?|crude|oil|gas)\b", "market_observation", positive=("draw", "decline", "fell"), negative=("build", "increase", "rose")),
    _rule("market.context", r"\b(?:broader market|overall market|market environment|market conditions|sector performance|risk sentiment)\b", "background"),
)


class NewsSynthesisEngine:
    def __init__(self, identity_index: IssuerIdentityIndex, *, registry_path: Path | None = None) -> None:
        self.identity_index = identity_index
        payload = json.loads((registry_path or Path(__file__).with_name("concept_registry.json")).read_text(encoding="utf-8"))
        self.registry_version = str(payload["registry_version"])
        allowed = {str(row["id"]) for row in payload["leaves"]}
        self.rules = tuple(rule for rule in RULES if rule.concept in allowed)

    def synthesize(self, source: Mapping[str, Any]) -> dict[str, Any]:
        source_id = str(source.get("source_id") or source.get("canonical_news_id") or "").strip()
        timestamp = str(source.get("source_timestamp") or source.get("published_at_utc") or "").strip()
        title = str(source.get("title") or "").strip()
        body = str(source.get("text") or source.get("rendered_text") or "").strip()
        text = body or title
        source_field = "rendered_text" if body and body != title else "title"
        if not source_id or not timestamp or not text:
            raise ValueError("source_id, source_timestamp, and source text are required")
        tickers = tuple(str(value) for value in source.get("tickers") or source.get("entity_terms") or () if value)
        entities = self.identity_index.resolve(text=text, candidates=tickers, timestamp=timestamp)
        envelope = _envelope(title, text, source, len(entities))
        statements, participations = self._statements(text, entities, source_field)
        flags = _quality_flags(source, entities, text)
        views = derive_issuer_views(entities, participations)
        synthesis = derive_synthesis(entities=entities, statements=statements, participations=participations, issuer_views=views)
        eligibility = derive_eligibility(entities=entities, statements=statements, participations=participations, envelope=envelope, quality_flags=flags)
        document = {
            "contract_version": CONTRACT_VERSION, "concept_registry_version": self.registry_version,
            "sample_id": source_id, "source_id": source_id, "source_timestamp": timestamp,
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "envelope": envelope,
            "entities": entities, "statements": statements, "participations": participations,
            "issuer_views": views, "synthesis": synthesis, "eligibility": eligibility,
            "quality_flags": flags,
            "production": {"production_version": PRODUCTION_VERSION, "engine_version": ENGINE_VERSION, "generated_at_utc": datetime.now(UTC).isoformat(), "source_revision": str(source.get("rendered_text_hash") or source.get("source_revision_key") or "")},
        }
        result = validate_document(document)
        if not result.valid:
            raise ValueError("invalid News Synthesis document: " + "; ".join(result.issues))
        return document

    def _statements(self, text: str, entities: Sequence[Mapping[str, Any]], source_field: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        statements: list[dict[str, Any]] = []
        participations: list[dict[str, Any]] = []
        previous_entity_ids: tuple[str, ...] = ()
        for start, end, quote in _sentence_spans(text):
            if len(quote) < 8:
                continue
            scoped_entities = _entities_for_quote(entities, quote, previous_entity_ids)
            if scoped_entities:
                previous_entity_ids = tuple(str(row["entity_id"]) for row in scoped_entities)
            matched_rules = [rule for rule in self.rules if rule.pattern.search(quote)]
            for rule in matched_rules:
                sid = f"s{len(statements) + 1:04d}"
                span = {"source_field": source_field, "start": start, "end": end, "quote": quote}
                statements.append({"statement_id": sid, "statement_kind": rule.statement_kind, "concept_leaf": rule.concept, "epistemic_status": _epistemic(quote), "time_relation": _time_relation(quote, rule.statement_kind), "evidence_spans": [span], "typed_facts": extract_typed_facts([span])})
                for entity in scoped_entities:
                    role = _semantic_role(quote, entity, rule.concept)
                    sentiment, strength = _sentiment(quote, rule, role)
                    participations.append({"statement_id": sid, "entity_id": entity["entity_id"], "semantic_role": role, "discourse_role": "none", "semantic_sentiment": sentiment, "sentiment_strength": strength})
        return statements, participations


def _envelope(title: str, text: str, source: Mapping[str, Any], entity_count: int) -> dict[str, Any]:
    combined = f"{title}\n{text}"
    metadata = " ".join(str(x) for name in ("channels", "provider_tags") for x in source.get(name) or ())
    author = str(source.get("author") or "").strip().casefold()
    article_url = str(source.get("article_url") or source.get("url_domain") or "").casefold()
    list_title = bool(re.search(r"\b(?:calendar|watch list|stocks to watch|top \d+|\d+ stocks|analyst color|price target changes)\b", title, re.I))
    market_overview = bool(re.search(r"\b(?:market|morning)\s+(?:wrap|overview|recap|update|capsule)\b|\bbig picture\b", combined, re.I))
    digest = bool(ROUNDUP_RE.search(title) or re.search(r"\b(?:movers|gainers|losers|market roundup|analyst ratings|stocks? to watch)\b", title, re.I))
    if list_title: structure = "reference_list"
    elif market_overview: structure = "market_overview"
    elif digest: structure = "multi_subject_digest"
    else: structure = "single_subject"
    if WHY_MOVING_RE.search(title) or re.search(r"\bwhy (?:the |is |are )?.{0,80}(?:stock|shares?) (?:is |are )?(?:moving|up|down|trading)\b", title, re.I): purpose = "explain_move"
    elif list_title or re.search(r"\b(?:ahead of|preview|what to expect|will report|scheduled|to watch)\b", title, re.I): purpose = "preview"
    elif structure in {"market_overview", "multi_subject_digest"}: purpose = "recap"
    elif re.search(r"\b(?:analysis|technical analysis|what investors should know|what you need to know|case for|bull case|bear case|valuation|outlook for)\b", combined, re.I): purpose = "analyze"
    else: purpose = "report"
    origin_evidence = {
        "analyst": bool(re.search(r"\b(?:analyst|research firm|brokerage|price target|rating|upgrade[sd]?|downgrade[sd]?)\b", combined, re.I)),
        "regulator": bool(re.search(r"\b(?:SEC|FDA|FTC|DOJ|regulator|regulatory agency|Federal Reserve|Census Bureau)\s+(?:said|reported|announced|approved|rejected|filed|released|issued|notified|ordered)\b|\b(?:SEC filing|FDA approval|regulatory filing)\b", combined, re.I)),
        "issuer": bool(re.search(r"\b(?:the company|management|the board|board of directors)\s+(?:announces?|reports?|said|approved|entered|expects?|reaffirms?|rejects?|declared)\b", combined, re.I) or re.search(r"^[^\n:]{2,120}\b(?:announces?|reports?|reaffirms?|expects?|sees|says|provides?|receives?|awarded|wins?|prices?|raises?|increases?|files?|confirms?|launches?|appoints?|acquires?|enters?|rejects?|declares?|posts?|regains?)\b", title, re.I)),
    }
    origins = [name for name, present in origin_evidence.items() if present]
    origin = "mixed" if len(origins) > 1 else origins[0] if origins else "editorial"
    automated = bool(AUTOMATED_RE.search(combined) or author in {"benzinga insights", "benzinga neuro"} or re.search(r"\b(?:benzinga insights|benzinga neuro|automatically generated|here's what the data shows)\b", combined, re.I))
    syndicated = bool(re.search(r"\b(?:press release|business wire|globe newswire|pr newswire|accesswire|zacks investment research)\b", combined, re.I) or re.search(r"businesswire|globenewswire|prnewswire|accesswire", article_url))
    aggregated = structure in {"multi_subject_digest", "reference_list"} or bool(re.search(r"\b(?:roundup|recap|here are|these stocks|analyst ratings)\b", title, re.I))
    if automated: production = "automated"
    elif syndicated: production = "syndicated"
    elif aggregated: production = "aggregated"
    elif author and author not in {"benzinga", "benzinga newsdesk"}: production = "original"
    else: production = "unknown"
    render_status = str(source.get("render_status") or "").strip().lower()
    availability = render_status if render_status in {"rendered", "title_only", "unrendered", "invalid"} else "rendered" if text and text != title else "title_only"
    evidence = _evidence(title or text)
    decision = lambda value, rule: {"value": value, "rule_id": rule, "evidence": evidence}
    return {"document_structure": decision(structure, "envelope.structure.v1"), "communication_purpose": decision(purpose, "envelope.purpose.v1"), "information_origin": decision(origin, "envelope.origin.v1"), "production_method": decision(production, "envelope.production.v1"), "text_availability": decision(availability, "envelope.text.v1")}


def _evidence(text: str) -> list[dict[str, Any]]:
    quote = text[:300].strip()
    return [{"source_field": "title", "start": 0, "end": len(quote), "quote": quote}] if quote else []


def _quality_flags(source: Mapping[str, Any], entities: Sequence[Mapping[str, Any]], text: str) -> list[str]:
    flags = {str(value) for name in ("quality_flags", "content_quality_flags") for value in source.get(name) or () if value}
    if not text.strip(): flags.add("invalid_text")
    if str(source.get("render_status") or "").strip().lower() == "unrendered": flags.add("unrendered_text")
    if not entities: flags.add("unresolved_identity")
    for row in entities:
        if row["identity_status"] in {"ambiguous", "unresolved"}: flags.add(f"{row['identity_status']}_identity")
    return sorted(flags)


def _epistemic(text: str) -> str:
    return "rumored" if re.search(r"\b(?:rumor|reportedly|may be|could be)\b", text, re.I) else "conditional" if re.search(r"\b(?:if|subject to)\b", text, re.I) else "planned" if re.search(r"\b(?:plans?|intends?|will)\b", text, re.I) else "expected" if re.search(r"\b(?:expects?|forecast|guidance)\b", text, re.I) else "confirmed"


def _time_relation(text: str, kind: str) -> str:
    if kind == "forecast" or re.search(r"\b(?:will|expects?|next year|future)\b", text, re.I): return "forward"
    if re.search(r"\b(?:previously|last (?:year|quarter|month)|historically)\b", text, re.I): return "historical"
    return "current"


def _sentiment(text: str, rule: ConceptRule, role: str) -> tuple[str, int]:
    if rule.statement_kind == "market_observation":
        return "neutral", 0
    normalized = text.casefold()
    if rule.concept == "analyst.rating_action":
        if re.search(r"\bdowngrad\w*\b", normalized): return "negative", 3
        if re.search(r"\bupgrad\w*\b", normalized): return "positive", 3
        if re.search(r"\b(?:sell|underperform|underweight)\b", normalized): return "negative", 2
        if re.search(r"\b(?:buy|outperform|overweight)\b", normalized): return "positive", 2
        return "neutral", 0
    if rule.concept == "analyst.price_target_action":
        if re.search(r"\b(?:cuts?|lowers?|reduc(?:e|es|ed))\b", normalized): return "negative", 2
        if re.search(r"\b(?:raises?|increases?|boosts?)\b", normalized): return "positive", 2
        return "neutral", 0
    if rule.concept == "corporate_transaction.acquisition":
        if role == "target": return "positive", 3
        if re.search(r"\b(?:dilutive|difficult|struggle|overpay|debt burden)\w*\b", normalized): return "negative", 2
        if re.search(r"\b(?:accretive|synerg|complementary|strategic fit)\w*\b", normalized): return "positive", 2
        return "neutral", 0
    positive = sum(term in normalized for term in rule.positive); negative = sum(term in normalized for term in rule.negative)
    if positive > negative: return "positive", min(4, 1 + positive)
    if negative > positive: return "negative", min(4, 1 + negative)
    return "neutral", 0


def _entity_in_quote(entity: Mapping[str, Any], quote: str) -> bool:
    ticker = re.escape(str(entity.get("ticker") or "")); name = str(entity.get("display_name") or "")
    return bool(ticker and re.search(rf"(?<![A-Z0-9])\$?{ticker}(?![A-Z0-9])", quote, re.I)) or bool(name and _normalize_alias(name) in _normalize_alias(quote))


def _entities_for_quote(
    entities: Sequence[Mapping[str, Any]], quote: str, previous_entity_ids: Sequence[str]
) -> list[Mapping[str, Any]]:
    explicit = [row for row in entities if _entity_in_quote(row, quote)]
    if explicit:
        return explicit
    if not re.search(r"\b(?:the company|it|its|management|the board|shares?|stock)\b", quote, re.I):
        return []
    if len(entities) == 1:
        return [entities[0]]
    previous = set(previous_entity_ids)
    inherited = [row for row in entities if str(row["entity_id"]) in previous]
    return inherited if len(inherited) == 1 else []


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    """Return exact source spans while preserving decimals and common abbreviations."""
    abbreviations = {"inc", "corp", "co", "ltd", "llc", "plc", "mr", "mrs", "ms", "dr", "st", "vs", "u.s", "a.m", "p.m"}
    spans: list[tuple[int, int, str]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        boundary = char in "!?\n"
        if char == ".":
            decimal = index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit()
            prefix = text[start:index].rstrip()
            token_match = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)$", prefix)
            abbreviation = bool(token_match and token_match.group(1).casefold().rstrip(".") in abbreviations)
            next_is_boundary = index + 1 == len(text) or text[index + 1].isspace()
            boundary = next_is_boundary and not decimal and not abbreviation
        if boundary:
            end = index + 1
            while end < len(text) and text[end] in ".!?": end += 1
            left = start
            while left < end and text[left].isspace(): left += 1
            right = end
            while right > left and text[right - 1].isspace(): right -= 1
            if left < right: spans.append((left, right, text[left:right]))
            start = end
            index = end
            continue
        index += 1
    left = start
    while left < len(text) and text[left].isspace(): left += 1
    right = len(text)
    while right > left and text[right - 1].isspace(): right -= 1
    if left < right: spans.append((left, right, text[left:right]))
    return spans


def _semantic_role(text: str, entity: Mapping[str, Any], concept: str) -> str:
    if concept == "corporate_transaction.acquisition":
        name = re.escape(str(entity.get("display_name") or entity.get("ticker") or ""))
        if name and re.search(rf"{name}.*\bacquir", text, re.I): return "acquirer"
        if name and re.search(rf"\bacquir.*{name}", text, re.I): return "target"
    return "affected_subject"


def _normalize_alias(value: str) -> str: return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))
def _safe_alias(value: str) -> bool: return len(value) >= 5 and value not in {"american", "capital", "global", "group", "international", "national", "united"}
def _as_date(value: str) -> date | None:
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try: return date.fromisoformat(value[:10])
        except ValueError: return None

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
EXCHANGE_PREFIX_RE = re.compile(
    r"^(?:NASDAQ|NYSE(?:\s+AMERICAN)?|NYSEAMERICAN|AMEX|OTC(?:QX|QB)?)\s*[:\-]\s*",
    re.I,
)


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
            ticker = _normalize_ticker_identifier(row.ticker)
            if not ticker:
                continue
            self._by_ticker.setdefault(ticker, []).append(row)
            for alias in row.aliases:
                for key in _alias_variants(alias):
                    if _safe_alias(key):
                        self._by_alias.setdefault(key, []).append(row)

    def resolve(self, *, text: str, candidates: Sequence[str], timestamp: str) -> list[dict[str, Any]]:
        day = _as_date(timestamp)
        explicit = {_normalize_ticker_identifier(value) for value in EXCHANGE_TICKER_RE.findall(text)} | {_normalize_ticker_identifier(value) for value in CASHTAG_RE.findall(text)}
        candidate_set = {_normalize_ticker_identifier(value) for value in candidates if value}
        explicit.discard("")
        candidate_set.discard("")
        matches: dict[str, set[str]] = {ticker: {"explicit_ticker_in_text"} for ticker in explicit}
        normalized_text = f" {_normalize_alias(text)} "
        for alias, rows in self._by_alias.items():
            if f" {alias} " not in normalized_text:
                continue
            valid = [row for row in rows if row.valid_on(day)]
            tickers = {row.ticker for row in valid}
            preferred = tickers & candidate_set
            if preferred:
                tickers = preferred
            elif candidate_set and len(alias.split()) == 1:
                continue
            issuer_ids = {row.issuer_id for row in valid if row.ticker in tickers}
            if len(tickers) == 1 or len(issuer_ids) == 1:
                for ticker in tickers:
                    matches.setdefault(ticker, set()).add(f"issuer_alias:{alias}")
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

    def supported_candidates(
        self,
        *,
        candidates: Sequence[str],
        timestamp: str,
    ) -> list[dict[str, Any]]:
        """Resolve exact provider candidates without asserting a text mention.

        The engine admits these rows only when the document independently contains
        an issuer-scoped semantic statement. Statement binding remains responsible
        for deciding whether the candidate actually participates in that statement.
        """
        day = _as_date(timestamp)
        entities: list[dict[str, Any]] = []
        tickers = sorted({_normalize_ticker_identifier(value) for value in candidates if value} - {""})
        for ticker in tickers:
            known = list(self._by_ticker.get(ticker, ()))
            valid = [row for row in known if row.valid_on(day)]
            identity = valid[0] if len({row.issuer_id for row in valid}) == 1 else None
            if identity is None:
                continue
            entities.append({
                "entity_id": identity.security_id or f"security:{identity.issuer_id}:{ticker}",
                "entity_kind": "security",
                "display_name": identity.display_name,
                "ticker": ticker,
                "identity_status": "resolved",
                "identity_evidence": ["provider_candidate_only"],
            })
        return entities

    def mention_terms(self, entity: Mapping[str, Any]) -> tuple[str, ...]:
        """Return canonical names that may bind a resolved entity to a statement."""
        ticker = _normalize_ticker_identifier(entity.get("ticker"))
        entity_id = str(entity.get("entity_id") or "")
        terms: list[str] = [str(entity.get("display_name") or ""), ticker]
        for row in self._by_ticker.get(ticker, ()):
            expected_id = row.security_id or f"security:{row.issuer_id}:{ticker}"
            if entity.get("identity_status") == "resolved" and expected_id != entity_id:
                continue
            terms.extend((row.display_name, *row.aliases))
        return tuple(dict.fromkeys(
            variant
            for term in terms
            for variant in _alias_variants(term)
            if variant
        ))


@dataclass(frozen=True, slots=True)
class ConceptRule:
    concept: str
    pattern: re.Pattern[str]
    statement_kind: str
    positive: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    local_evidence: bool = False


def _rule(
    concept: str,
    terms: str,
    kind: str = "event",
    *,
    positive: Sequence[str] = (),
    negative: Sequence[str] = (),
    local_evidence: bool = False,
) -> ConceptRule:
    return ConceptRule(
        concept,
        re.compile(terms, re.I),
        kind,
        tuple(positive),
        tuple(negative),
        local_evidence,
    )


RULES = (
    _rule("analyst.rating_action", r"\b(?:upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?|rates?|ratings?)\b(?:.{0,100})\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|equal[- ]weight|sector perform|market perform|rating)\b|\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|equal[- ]weight|sector perform|market perform)\s+rating\b|\banalysts? (?:have )?(?:provided|published|offered).{0,60}ratings?\b", "assessment", positive=("upgrade", "buy", "outperform", "overweight"), negative=("downgrade", "sell", "underperform", "underweight")),
    _rule("analyst.price_target_action", r"\b(?:price target|target price|price objective|PO|P/T|\$\d+(?:\.\d+)? target|target on)\b", "forecast", positive=("raises", "raised", "higher", "increases"), negative=("cuts", "cut", "lowers", "lowered")),
    _rule("earnings.performance", r"\b(?:earnings|EPS|revenues?|sales|net income|profit|quarterly results?|financial results?)\b.{0,180}\b(?:reports?|reported|beat[sd]?|miss(?:es|ed)?|above|below|better[- ]than[- ]expected|weaker[- ]than[- ]expected|rose|fell|declin(?:e|ed)|grew|increase[sd]?|decrease[sd]?|loss|up from|down from|narrowed|widened)\b|\b(?:reports?|reported|beat[sd]?|miss(?:es|ed)?|rose|fell|grew|narrowed|widened)\b.{0,100}\b(?:earnings|EPS|revenues?|sales|profit|results?|loss)\b", positive=("beat", "above", "better-than-expected", "grew", "rose", "record", "increase", "up from", "narrowed"), negative=("miss", "below", "weaker-than-expected", "fell", "decline", "decrease", "loss", "down from", "widened")),
    _rule("guidance.issued", r"\b(?:issues?|provid(?:e|es|ed)|guid(?:e|es|ed)|raises?|lower(?:s|ed)?|cuts?|reaffirm(?:s|ed|ing)?|withdraws?|updates?)\b.{0,100}\b(?:guidance|outlook|forecast|revenue|sales|earnings|EPS|EBITDA|growth|margin)\b|\b(?:guidance|outlook)\b.{0,100}\b(?:raised|lowered|cut|reaffirmed|withdrawn|unchanged|expects?)\b|\b(?:sees|expects?|anticipates?|projects?|is looking for)\b.{0,120}\b(?:revenue|sales|earnings|EPS|EBITDA|growth|margin)\b", "forecast", positive=("raise", "increas", "higher"), negative=("cut", "lower", "withdraw", "reduce", "weaker")),
    _rule("corporate_transaction.acquisition", r"\b(?:acquir(?:e|es|ed|ing)|acquisition|merger|takeover)\b|\bbuys?\b.{1,100}\bfor\s+\$|\bpurchase(?:s|d)? of .{0,100}\b(?:assets?|business|operations?)\b|\b(?:rumored?|possible|potential)\s+bid for\b|\b(?:will|would|agrees? to) combine with\b|\bamalgamat(?:e|es|ed|ing) with\b|\b(?:complet(?:e|es|ed|ion) of|proposed) (?:the )?(?:business )?combination\b", positive=("agreed", "complete", "closes", "approved", "purchase", "will combine", "amalgamat"), negative=("terminate", "withdraw", "no longer pursue", "blocked", "reject", "not in best interest")),
    _rule("corporate_transaction.asset_sale", r"\b(?:asset sale|sale of .{0,100}(?:assets?|business|operations?)|closes? (?:the )?sale of|divest(?:s|ed|iture)|sell(?:s|ing)? its .*business)\b", positive=("complete", "closes", "proceeds"), negative=("distress",)),
    _rule("capital.financing", r"\b(?:public offering|registered direct offering|private placement|mixed shelf|shelf (?:offering|registration)|at-the-market|ATM (?:program|offering)|convertible (?:senior )?notes?|debt financing|equity financing|files? for .{0,80}offering|prices? .{0,80}(?:offering|shares?|notes?|bonds?)|offer(?:s|ed|ing)? .{0,60} shares?|shares? offering|offering of .{0,80}(?:shares?|notes?|units?|securities)|sale (?:by us )?of .{0,80}(?:common stock|preferred stock|debt securities|warrants)|investment from .{0,80}funds?|term sheet .{0,100}investment|conversion price .{0,40}(?:share|stock))\b", positive=("investment from",), negative=("dilution", "offering", "placement", "convertible", "shelf", "prices")),
    _rule("capital.return", r"\b(?:share repurchase|buyback|dividend|capital return)\b", positive=("restart", "increase", "raises", "special dividend", "fund capital return", "capital return"), negative=("suspend", "cut", "reduce")),
    _rule("regulatory.action", r"\b(?:trading halt|halted|resume trading|SEC action|regulatory action|compliance notice|formal investigation|clinical hold|license renewal|crackdown|advisory committee|regulator|letter of authorization|conditions? of authorization|reporting requirements?)\b|\b(?:FDA|FTC|SEC|European Commission|Nuclear Regulatory Commission)\b.{0,180}\b(?:investigation|hold|cancel|issue|renew|order|action|notice|authoriz|reporting requirement)\w*\b", positive=("authorize", "authorization", "renew"), negative=("halt", "suspend", "noncompliance", "investigation", "crackdown", "cancel", "myocarditis", "pericarditis", "adverse")),
    _rule("clinical.regulatory_milestone", r"\b(?:FDA|EMA|NDA|BLA)\b.{0,180}\b(?:approv|reject|complete response|clinical hold|clearance|accept|authoriz|resubmission|acknowledge|submission|meeting|grant)\w*\b|\b(?:complete response letter|clinical hold|primary endpoint|phase [123] (?:study|trial)|letter of authorization|regulatory submission)\b", positive=("approve", "approval", "clearance", "accept", "authorize", "authorization", "grant", "met primary"), negative=("reject", "complete response", "hold", "did not meet", "missed", "myocarditis", "pericarditis", "cancel")),
    _rule("clinical.trial_result", r"\b(?:clinical trial|study|Phase [123])\b.*\b(?:endpoint|results?|data|efficacy|safety)\b", positive=("met", "positive", "improved"), negative=("failed", "missed", "adverse")),
    _rule("legal.proceeding", r"\b(?:lawsuit|litigation|investigation|subpoena|settlement|arbitration|legal claim|claim for .{0,60}damages|seeking .{0,40}damages)\b", positive=("seeking damages", "served a request", "files arbitration"), negative=("lawsuit", "investigation", "subpoena", "breach", "discriminatory", "adverse treatment")),
    _rule("listing.market_structure", r"\b(?:reverse split|stock split|delisting|listing compliance|regains? compliance|regained compliance|continued listing|non[- ]compliance|minimum bid|late filing|failure to timely file|included in .{0,60}(?:Russell|S&P|Nasdaq).{0,20}index|IPO)\b", positive=("regain", "regained compliance", "approved listing", "included"), negative=("delisting", "noncompliance", "non-compliance", "late", "failure", "reverse split")),
    _rule("commercial.contract", r"\b(?:awarded|wins?|receives?|secures?|signs?|enters?|affirms?)\b.{0,120}\b(?:contract|order|award|agreement|program|initiative)\b|\b(?:contract|agreement)\s+(?:termination|cancellation|non[- ]renewal)\b|\b(?:termination|cancellation|non[- ]renewal)\b.{0,80}\b(?:contract|agreement)\b|\b(?:follow[- ]on )?(?:contract|order|agreement|program|initiative)\b.{0,120}\b(?:awarded|affirmed|won|win|received|secured|signed|terminated|cancelled|canceled|not renewed)\b", positive=("awarded", "affirmed", "wins", "win", "received", "secured", "signed"), negative=("cancel", "terminate", "non-renewal", "not renewed")),
    _rule("product.milestone", r"\b(?:launch|unveil|reveal|debut|showcas|commercializ|introduc|roll(?:s|ed)? out|recall|discontinue|authoriz(?:e|es|ed|ing)|release(?:s|d)? (?:final )?pricing|production milestone|assembly line|built? \d+)\w*\b.{0,120}\b(?:product|platform|service|device|drug|treatment|vaccine|vehicle|system|game|headset|candidate|model|factory|use)\b|\b(?:product|device|drug|treatment|vaccine|service|game|headset|vehicle|model)\b.{0,120}\b(?:launch|unveil|reveal|debut|showcas|commercializ|introduc|recall|discontinue|delay|authoriz|assembly line)\w*\b|\b(?:new products?|product delay|(?:lead|investigational) (?:product candidate|drug|treatment|therapy|antibody)|delivery system|bodies coming down the assembly line)\b", positive=("launch", "unveil", "reveal", "debut", "commercializ", "introduc", "approval", "authorize", "new", "assembly", "built", "affordable"), negative=("recall", "delay", "discontinue")),
    _rule("governance.management_change", r"\b(?:appoints?|names?|elects?|resigns?|retires?|steps down|terminates?|replaces?)\b.{0,100}\b(?:chief executive|chief financial|CEO|CFO|president|director|board)\b|\b(?:chief executive|chief financial|CEO|CFO|president|director)\b.{0,80}\b(?:resigns?|retires?|steps down|appointed|named|terminated|replaced)\b", negative=("resign", "terminated", "steps down")),
    _rule("operations.business_update", r"\b(?:business update|restructur|layoff|shutdown|expansion|job cuts?|workforce reduction|service unaffected|operations? unaffected|opens? (?:a )?(?:store|facility|dispensary)|business performance)\w*\b", positive=("expansion", "growth", "unaffected", "opens"), negative=("layoff", "shutdown", "restructur", "cuts")),
    _rule("earnings.release_schedule", r"\b(?:will (?:report|release|post)|will be reporting|scheduled to report|set to (?:report|announce)|reports? .{0,60} on (?:Monday|Tuesday|Wednesday|Thursday|Friday)|release earnings results|release .{0,40} financial results|earnings (?:date|call beginning|release)|after (?:the )?(?:opening|closing) bell|before (?:the )?opening bell|after market (?:close|hours)|ahead of .{0,30}(?:Q[1-4]|quarterly) earnings .{0,30}(?:Monday|Tuesday|Wednesday|Thursday|Friday))\b", "reference"),
    _rule("earnings.restatement", r"\b(?:restate|restatement|should no longer be relied upon)\b", negative=("restate", "no longer be relied")),
    _rule("capital.deleveraging", r"\b(?:deleverag|debt repayment|repayment of (?:outstanding )?(?:debt|borrowings)|repay(?:s|ed)? .*debt|reduce(?:s|d)? .*debt)\w*\b", positive=("deleverag", "repay", "repayment", "reduce")),
    _rule("capital.structure", r"\b(?:authorized shares|outstanding shares|share consolidation|capital structure|refinanc\w*|repurchas\w* .{0,80}(?:notes?|bonds?|debt)|convertible bonds?)\b", positive=("refinanc", "repurchas", "extend", "later maturity")),
    _rule("credit.solvency", r"\b(?:bankrupt|chapter 11|default|going concern|insolven|liquidity crisis)\w*\b", negative=("bankrupt", "default", "going concern", "insolven", "crisis")),
    _rule("financial.margin", r"\b(?:gross|operating|EBITDA|profit) margins?\b", positive=("expand", "improv", "increase", "accretive"), negative=("contract", "compress", "declin", "dilutive", "difficult", "struggle")),
    _rule("financial.operating_performance", r"\b(?:operating income|operating loss|OIBDA|EBITDA|profitability|net income|net loss|operating profit|results? of operations|return on (?:equity|assets)|ROE|ROA|comparable store (?:sales|net sales)|business performance)\b|\b(?:revenues?|sales)\b.{0,100}\b(?:rose|climbed|grew|growth|fell|slipped|declined|decreased|increased|vs\.?|compared with|year[- ]over[- ]year)\b", positive=("income", "profitab", "improv", "increase", "grew", "growth", "rose", "climbed", "recovered"), negative=("loss", "declin", "deterior", "decrease", "fell", "slipped")),
    _rule("financial.cash_flow", r"\b(?:free cash flow|operating cash flow|cash burn)\b", positive=("positive", "increase", "improv"), negative=("negative", "burn", "declin")),
    _rule("financial.liquidity", r"\b(?:cash runway|liquidity|cash and equivalents|working capital)\b", positive=("strong", "sufficient", "improv"), negative=("shortfall", "insufficient", "weak")),
    _rule("financial.loss_exposure", r"\b(?:impairment|write[- ]?down|charge|loss exposure)\b", negative=("impairment", "write", "charge", "loss")),
    _rule("financial.internal_control", r"\b(?:material weakness|internal controls?|control deficiency)\b", negative=("weakness", "deficiency", "ineffective")),
    _rule("financial.credit_quality", r"\b(?:credit rating|credit quality|rating agency)\b", positive=("upgrade", "improv"), negative=("downgrade", "deterior")),
    _rule("financial.credit_quality", r"\b(?:card |credit-card |loan )?delinquenc(?:y|ies)\b[^,;.!?]{0,40}\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b|\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b[^,;.!?]{0,20}\b(?:card |credit-card |loan )?delinquenc(?:y|ies)\b", positive=("down", "lower", "decreas"), negative=("up", "higher", "increas"), local_evidence=True),
    _rule("financial.credit_quality", r"\b(?:credit-card |loan )?(?:write-offs?|charge-offs?)\b[^,;.!?]{0,40}\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b|\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b[^,;.!?]{0,20}\b(?:credit-card |loan )?(?:write-offs?|charge-offs?)\b", positive=("down", "lower", "decreas"), negative=("up", "higher", "increas"), local_evidence=True),
    _rule("estimate.revision", r"\b(?:estimates?|consensus)\b.{0,180}\b(?:rais(?:e|ed|ing)|lower(?:ed|ing)?|revis(?:e|ed|ing)|cut|increas(?:e|ed|ing)|reduc(?:e|ed|ing)|come down|adjust(?:ed|ing))\b|\b(?:rais(?:e|ed|ing)|lower(?:ed|ing)?|revis(?:e|ed|ing)|cut|increas(?:e|ed|ing)|reduc(?:e|ed|ing)|adjust(?:ed|ing))\b.{0,180}\b(?:estimates?|consensus)\b", "forecast", positive=("raise", "higher", "increase"), negative=("lower", "cut", "reduce", "come down")),
    _rule("ownership.position_change", r"\b(?:stake|ownership|position in|shares? of)\b.{0,160}\b(?:increased|decreased|sold|bought|acquired|trimmed|exited)\b|\b(?:increased|decreased|sold|bought|acquired|trimmed|exited)\b.{0,160}\b(?:stake|position in|shares? of)\b", positive=("increased", "bought", "acquired"), negative=("decreased", "sold", "trimmed", "exited")),
    _rule("ownership.position", r"\b(?:owns? .{0,80}(?:shares?|stake)|ownership (?:stake|interest)|beneficial owner|stake in|position in .{0,80}(?:stock|shares?|company))\b", "background"),
    _rule("commercial.partnership", r"\b(?:partnership|collaboration|strategic alliance|joint venture)\b", positive=("partnership", "collaboration", "alliance")),
    _rule("commercial.demand_condition", r"\b(?:strong|robust|growing|increas(?:e|ed|ing)|record|higher|weak|soft|slower|lower|declin(?:e|ed|ing)|falling|delayed|pent-up)\b.{0,80}\b(?:demand|bookings|orders?|backlog|customer additions?|subscribers?|appetite)\b|\b(?:customer|consumer|client|market) demand\b|\bdemand (?:for|from)\b|\bappetite for\b|\b(?:bookings|orders?|backlog|subscribers?)\b.{0,80}\b(?:grew|rose|increased|declined|fell|decreased|record|strong|weak)\b|\b(?:customers? lost|shortages?|client purchasing)\b", positive=("strong", "robust", "growing", "increase", "record", "higher", "appetite"), negative=("weak", "soft", "declin", "slow", "lower", "lost", "shortage", "delayed", "falling")),
    _rule("commercial.competitive_position", r"\b(?:market share|competitive position|competition|competitor|competitive (?:advantage|disadvantage)|market leader|largest .{0,50}(?:company|provider|operator|producer)|first and only|well-positioned)\b", positive=("gain", "leading", "leader", "largest", "advantage", "first and only", "well-positioned"), negative=("lose", "pressure", "challeng", "disadvantage", "discriminatory")),
    _rule("operations.workforce", r"\b(?:layoffs?|job cuts?|workforce reduction|hires?|headcount|seasonal jobs?|creat(?:e|es|ed|ing) .{0,50}jobs?|jobs? (?:created|during construction)|employs? (?:over|approximately|about|more than)?\s*\d|workforce of \d|convert(?:s|ed|ing)? .{0,50}employees? (?:into|to)|welcome back .{0,30}workers?|train .{0,30}(?:new )?(?:workers?|employees?))\b", positive=("hire", "create", "convert", "welcome back"), negative=("layoff", "cut", "reduction")),
    _rule("operations.capacity_change", r"\b(?:capacity|facility|plant|factory|fleet|operational scale|operating footprint|development inventory)\b.{0,160}\b(?:add|expand|open|close|shutdown|increase|reduce|double|order)\w*\b|\b(?:add|expand|open|close|shutdown|increase|reduce|double|order)\w*\b.{0,160}\b(?:capacity|facility|plant|factory|fleet|aircraft|airplanes?|operational scale|operating footprint|development inventory)\b", positive=("add", "expand", "open", "increase", "double", "order"), negative=("close", "shutdown", "reduce")),
    _rule("governance.auditor_change", r"\b(?:auditor|accounting firm)\b.*\b(?:resign|dismiss|appoint|replace)\w*\b", negative=("resign", "dismiss")),
    _rule("governance.shareholder_vote", r"\b(?:shareholders?|stockholders?)\b.*\b(?:vote|meeting|proposal|proxy|director nominees?)\b|\b(?:proxy|director nominees?)\b.*\b(?:shareholders?|stockholders?)\b"),
    _rule("index.membership", r"\b(?:added to|removed from|join(?:s|ed)?|delete(?:d)?|replac(?:e|es|ed|ing))\b.*\b(?:index|S&P|Russell|Nasdaq-100)\b|\b(?:index|S&P|Russell|Nasdaq-100)\b.*\b(?:added|removed|join(?:s|ed)?|delete(?:d)?|replac(?:e|es|ed|ing))\b", positive=("added", "join"), negative=("removed", "delete")),
    _rule("technology.cybersecurity_incident", r"\b(?:cyberattack|data breach|ransomware|security incident)\b", negative=("attack", "breach", "ransomware", "incident")),
    _rule("market.options_activity", r"\b(?:options activity|call volume|put volume|unusual options)\b", "market_observation"),
    _rule("market.short_interest_observed", r"\b(?:short interest|short volume|days to cover)\b", "market_observation"),
    _rule("market.technical_analysis", r"\b(?:moving average|RSI|MACD|technical analysis|support (?:level|zone)|resistance (?:level|zone)|price support|price resistance)\b", "assessment"),
    _rule("macro.inflation", r"\b(?:inflation|consumer price index|CPI|producer price index|PPI)\b", "background"),
    _rule("macro.employment", r"\b(?:employment|unemployment|nonfarm payrolls?|jobless claims)\b", "background"),
    _rule("macro.economic_outlook", r"\b(?:economic outlook|recession|economic expansion)\b", "forecast"),
    _rule("financial.interest_rate", r"\b(?:interest rates?|rate hike|rate cut|federal funds rate)\b", "background"),
    _rule("market.price_move_observed", r"\b(?:shares?|stock|equity|bitcoin|BTC|memecoin)\b.{0,100}\b(?:rose|fell|falls?|gained|lost|dropped|surged|slid|slipped|jumped|rallied|declined|trading at|trading (?:up|down)|traded at|closed at|open(?:ed)? (?:at|for trade at)|is (?:up|down|higher|lower)|moved (?:above|below)|higher|lower|spike[sd]?|up \d|down \d)\b|\b(?:rose|fell|falls?|gained|lost|dropped|surged|slid|slipped|jumped|rallied|declined|traded (?:up|down)|slumped|spike[sd]?|selling off)\b.{0,100}\b(?:shares?|stock|equity|by \d|to \$)\b|\bclosed(?: yesterday| (?:on )?(?:Monday|Tuesday|Wednesday|Thursday|Friday))? at \$\d|\b(?:is|are|was|were|trading)\s+(?:up|down|higher|lower)\s+(?:by|over|roughly|more than)?\s*\d+(?:\.\d+)?%|^[^.!?\n]{1,100}\b(?:falls?|rises?|drops?|jumps?|surges?|slumps?|dips?)\b|\b[A-Z]{1,5}\)?\s+(?:shares?\s+)?(?:up|down|falls?|gains?|\+|-)[ ]?\d+(?:\.\d+)?%|\([A-Z]{1,5}:\s*\d+(?:\.\d+)?,\s*[+-]?\d+(?:\.\d+)?,\s*[+-]?\d+(?:\.\d+)?%\)", "market_observation", positive=("rose", "rises", "gained", "surged", "jumped", "rallied", "higher", "trading up", "moved above", "spike"), negative=("fell", "falls", "lost", "dropped", "drops", "slid", "slipped", "declined", "lower", "trading down", "moved below", "slumped", "selling off")),
    _rule("market.currency_move_observed", r"\b(?:dollar index|currency|forex|euro|yen|yuan|pound sterling)\b.{0,100}\b(?:rose|fell|gained|lost|higher|lower|trading at|up|down)\b", "market_observation"),
    _rule("market.volume_move_observed", r"\b(?:trading volume|volume spike|unusual volume)\b", "market_observation"),
    _rule("market.trading_status", r"\b(?:halted|trading halt|resumed trading)\b", "market_observation"),
    _rule("market.money_flow_observed", r"\b(?:money flows?|fund flows?|inflows?|outflows?|buying pressure|selling pressure)\b", "market_observation", positive=("positive", "inflow", "buying"), negative=("negative", "outflow", "selling")),
    _rule("analyst.issuer_assessment", r"\b(?:analysts?|brokerage|research firm|investment firm)\b.{0,180}\b(?:believes?|expects?|sees?|views?|said|positive|negative|bullish|bearish|upside|downside|recommend(?:s|ed)?|confident)\b", "assessment", positive=("positive", "bullish", "upside", "strong", "well-positioned", "recommend", "confident"), negative=("negative", "bearish", "downside", "weak")),
    _rule("strategy.valuation_assessment", r"\b(?:valuation|valued|multiple|price[- ]to[- ]earnings|P/E|PE|PEG ratio|undervalued|overvalued|cheap|expensive|fully reflect|buyer (?:at|around))\b", "assessment", positive=("undervalued", "cheap", "attractive", "buyer"), negative=("overvalued", "expensive", "premium", "fully reflect")),
    _rule("operations.cost_efficiency", r"\b(?:cost savings?|cost reduction|reduce(?:s|d|ing)? (?:its )?costs?|reduc(?:e|es|ed|ing) operating expenses?|expense reduction|efficiency program|productivity initiative|annual(?:ized)? savings|savings (?:in|on|from) .{0,50}costs?|lower .{0,40}costs?|(?:rising|higher|increased) (?:labor )?costs?|costs? (?:rose|risen|rising|increased|dropped|declined|decreased)|contain costs?|control .{0,30}costs?|total cost of ownership|cost[- ]effectiveness)\b", positive=("savings", "reduction", "reduce", "lower", "efficiency", "productivity", "dropped", "declined"), negative=("higher costs", "rising costs", "rising labor costs", "increased costs", "cost pressure")),
    _rule("macro.policy_outlook", r"\b(?:central bank|Federal Reserve|Fed|government|policy makers?)\b.{0,160}\b(?:policy|stimulus|rate cuts?|rate hikes?|tighten|ease|intervention)\b|\b(?:monetary|fiscal) policy\b", "forecast"),
    _rule("commodity.inventory", r"\b(?:crude oil|oil|natural gas|gasoline) inventor(?:y|ies)\b|\binventor(?:y|ies)\b.{0,80}\b(?:barrels?|crude|oil|gas)\b", "market_observation", positive=("draw", "decline", "fell"), negative=("build", "increase", "rose")),
    _rule("strategy.operational_priority", r"\b(?:strategic priority|operational priority|focus(?:ed|es|ing)? on|plans? to prioritize|key initiative|strategic objective)\b", "assessment"),
    _rule("market.context", r"\b(?:broader market|overall market|market environment|market conditions|sector performance|risk sentiment|market capitalization|52-week (?:high|low)|outperform(?:ed|s|ing)? the market|underperform(?:ed|s|ing)? the market|market open)\b", "background"),
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
        if _has_issuer_scoped_rule(text, self.rules):
            entity_ids = {str(entity["entity_id"]) for entity in entities}
            for candidate in self.identity_index.supported_candidates(
                candidates=tickers,
                timestamp=timestamp,
            ):
                if str(candidate["entity_id"]) not in entity_ids:
                    entities.append(candidate)
                    entity_ids.add(str(candidate["entity_id"]))
            entities.sort(key=lambda row: (str(row.get("ticker") or ""), str(row["entity_id"])))
        envelope = _envelope(title, text, source)
        mention_terms = {
            str(entity["entity_id"]): self.identity_index.mention_terms(entity)
            for entity in entities
        }
        statements, participations = self._statements(text, entities, source_field, mention_terms)
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

    def _statements(
        self,
        text: str,
        entities: Sequence[Mapping[str, Any]],
        source_field: str,
        mention_terms: Mapping[str, Sequence[str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        statements: list[dict[str, Any]] = []
        participations: list[dict[str, Any]] = []
        previous_entity_ids: tuple[str, ...] = ()
        previous_end = 0
        for start, end, quote in _sentence_spans(text):
            if len(quote) < 8:
                continue
            if _boilerplate_sentence(quote):
                previous_end = end
                continue
            if "\n\n" in text[previous_end:start]:
                previous_entity_ids = ()
            matched_rules = [
                (rule, match)
                for rule in self.rules
                if (match := rule.pattern.search(quote)) and _rule_applicable(rule, quote)
            ]
            inherit_subject = any(_issuer_scoped_concept(rule.concept) for rule, _match in matched_rules)
            scoped_entities = _entities_for_quote(
                entities,
                quote,
                previous_entity_ids,
                mention_terms,
                inherit_subject=inherit_subject,
            )
            if scoped_entities:
                previous_entity_ids = tuple(str(row["entity_id"]) for row in scoped_entities)
            for rule, match in matched_rules:
                sid = f"s{len(statements) + 1:04d}"
                statement_quote = match.group(0) if rule.local_evidence else quote
                statement_start = start + match.start() if rule.local_evidence else start
                statement_end = start + match.end() if rule.local_evidence else end
                span = {
                    "source_field": source_field,
                    "start": statement_start,
                    "end": statement_end,
                    "quote": statement_quote,
                }
                typed_facts = extract_typed_facts([span])
                statements.append({"statement_id": sid, "statement_kind": rule.statement_kind, "concept_leaf": rule.concept, "epistemic_status": _epistemic(quote), "time_relation": _time_relation(quote, rule.statement_kind), "evidence_spans": [span], "typed_facts": typed_facts})
                for entity in scoped_entities:
                    role = _semantic_role(quote, entity, rule.concept)
                    sentiment, strength = _sentiment(
                        statement_quote,
                        rule,
                        role,
                        typed_facts,
                        entity=entity,
                        mention_terms=mention_terms.get(str(entity["entity_id"]), ()),
                    )
                    participations.append({"statement_id": sid, "entity_id": entity["entity_id"], "semantic_role": role, "discourse_role": "none", "semantic_sentiment": sentiment, "sentiment_strength": strength})
            previous_end = end
        return statements, participations


def _envelope(
    title: str,
    text: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    combined = f"{title}\n{text}"
    metadata = " ".join(str(x) for name in ("channels", "provider_tags") for x in source.get(name) or ())
    author = str(source.get("author") or "").strip().casefold()
    article_url = str(source.get("article_url") or source.get("url_domain") or "").casefold()
    list_title = bool(re.search(r"\b(?:calendar|watch list|stocks to watch|top \d+|\d+ stocks|analyst color|price target changes)\b", title, re.I))
    market_overview = bool(re.search(
        r"\b(?:market|morning)\s+(?:wrap|overview|recap|update|capsule)\b|\bbig picture\b|"
        r"\b(?:stock|index|treasury|commodity|currency|forex|cattle|sugar|natural gas|crude oil) futures?\b|"
        r"\b(?:dollar index|forex:|wall street|what'?s moving markets|S&P 500 (?:turns|futures?|extends?|falls?|rises?))\b|"
        r"\b(?:jobless claims|gross domestic product|GDP|consumer prices?|CPI|pending home sales|wholesale inventories|crude oil inventories)\b|"
        r"\bBitcoin\b.{0,120}\bEthereum\b",
        combined,
        re.I,
    ))
    digest = bool(ROUNDUP_RE.search(title) or re.search(r"\b(?:movers|gainers|losers|market roundup|analyst ratings|stocks? to watch|top (?:upgrades|downgrades)|catalysts? to watch|tweets? for)\b", title, re.I))
    if list_title: structure = "reference_list"
    elif market_overview: structure = "market_overview"
    elif digest: structure = "multi_subject_digest"
    else: structure = "single_subject"
    causal_mover = bool(
        WHY_MOVING_RE.search(title)
        or re.search(r"\b(?:why|what(?:'s| is) (?:up|going on) with)\b.{0,100}\b(?:shares?|stock)\b", title, re.I)
        or re.search(r"\b(?:shares?|stock)\b.{0,80}\b(?:up|down|higher|lower|rall(?:y|ies|ied|ying)|surg(?:e|es|ed|ing)|soar(?:s|ed|ing)?|spik(?:e|es|ed|ing)|jump(?:s|ed|ing)?|fall(?:s|ing)?|fell|rose|gain(?:s|ed)?|drop(?:s|ped|ping)?|slid(?:e|ing)?|sink(?:s|ing)?|sank|crash(?:es|ed|ing)?|hammered|unaffected)\b.{0,80}\b(?:after|following|on|as|amid|because|here'?s why)\b", title, re.I)
        or re.search(r"\b(?:shares?|stock)\s+(?:are\s+)?trading\s+(?:up|down|higher|lower)\b.{0,80}\b(?:after|following|on|as|amid)\b", title, re.I)
    )
    if causal_mover and not market_overview: purpose = "explain_move"
    elif list_title or re.search(
        r"\b(?:ahead of|preview|what to expect|will report|to watch)\b|"
        r"\bscheduled to (?:report|announce|release|publish)\b",
        title,
        re.I,
    ): purpose = "preview"
    elif structure in {"market_overview", "multi_subject_digest"}: purpose = "recap"
    elif re.search(r"\b(?:analysis|technical analysis|what investors should know|what you need to know|case for|bull case|bear case|valuation|outlook for)\b", combined, re.I): purpose = "analyze"
    else: purpose = "report"
    analyst_match = _first_match(
        (
            r"\b(?:analysts?(?!\s+(?:est\.?|estimates?|consensus|expectations)\b)|research firm|price target|rating|upgrade[sd]?|"
            r"downgrade[sd]?|initiates?|maintains?|reiterates?)\b",
        ),
        title,
        text,
    )
    regulator_match = _first_match((
        r"\b(?:SEC|FDA|FTC|DOJ|regulator|regulatory agency|Federal Reserve|Census Bureau)\s+"
        r"(?:said|reported|announced|approved|rejected|filed|released|issued|notified|ordered)\b|"
        r"\b(?:SEC filing|FDA approval|regulatory filing)\b",
    ), title, text)
    issuer_body_match = _first_match((
        r"\b(?:the company|management|the board|board of directors)\s+"
        r"(?:announces?|reports?|said|approved|entered|expects?|reaffirms?|rejects?|declared)\b",
    ), title, text)
    issuer_headline_match = _first_match((
        r"^[^\n:]{2,120}\b(?:announces?|reports?|reaffirms?|expects?|sees|says|"
        r"provides?|receives?|awarded|wins?|prices?|files?|confirms?|launches?|"
        r"appoints?|acquires?|enters?|rejects?|declares?|posts?|regains?)\b",
    ), title, "")
    analyst_origin = analyst_match is not None
    regulator_origin = regulator_match is not None
    issuer_match = issuer_body_match or (
        issuer_headline_match if analyst_match is None else None
    )
    issuer_origin = issuer_match is not None
    editorial_origin = purpose == "explain_move" and any(
        (analyst_origin, regulator_origin, issuer_origin)
    )
    editorial_match = _first_match((r".+",), title, "") if editorial_origin else None
    origin_evidence = {
        "analyst": analyst_origin,
        "regulator": regulator_origin,
        "issuer": issuer_origin,
        "editorial": editorial_origin,
    }
    origins = [name for name, present in origin_evidence.items() if present]
    origin = "mixed" if len(origins) > 1 else origins[0] if origins else "editorial"
    if (
        purpose == "report"
        and analyst_match is not None
        and not issuer_origin
        and not regulator_origin
    ):
        purpose = "analyze"
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
    default_evidence = _evidence(title or text)
    origin_matches = {
        "analyst": analyst_match,
        "regulator": regulator_match,
        "issuer": issuer_match,
        "editorial": editorial_match,
    }
    exact_origin_evidence = [
        _match_evidence(match)
        for name in origins
        if (match := origin_matches[name]) is not None
    ]
    decision = lambda value, rule, evidence=default_evidence: {
        "value": value,
        "rule_id": rule,
        "evidence": evidence,
    }
    return {
        "document_structure": decision(structure, "envelope.structure.v1"),
        "communication_purpose": decision(
            purpose,
            "envelope.purpose.v1",
            [_match_evidence(analyst_match)] if purpose == "analyze" and analyst_match is not None else default_evidence,
        ),
        "information_origin": decision(origin, "envelope.origin.v1", exact_origin_evidence),
        "production_method": decision(production, "envelope.production.v1"),
        "text_availability": decision(availability, "envelope.text.v1"),
    }


def _first_match(
    patterns: Sequence[str],
    title: str,
    text: str,
) -> tuple[str, re.Match[str]] | None:
    for source_field, value in (("title", title), ("rendered_text", text)):
        for pattern in patterns:
            if match := re.search(pattern, value, re.I | re.S):
                return source_field, match
    return None


def _match_evidence(match: tuple[str, re.Match[str]]) -> dict[str, Any]:
    source_field, value = match
    return {
        "source_field": source_field,
        "start": value.start(),
        "end": value.end(),
        "quote": value.group(0),
    }


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


def _rule_applicable(rule: ConceptRule, text: str) -> bool:
    """Apply cross-cutting semantic gates that cannot be expressed safely as noun regexes."""
    if rule.concept == "guidance.issued":
        external_expectation = re.search(r"\b(?:analysts? expect|analysts? estimate|bulls? (?:will )?hope|predictions? for|consensus (?:calls|expects))\b", text, re.I)
        explicit_issuer_action = re.search(
            r"\b(?:issue|issued|issuing|provided|guid(?:e[sd]?|ing)|rais(?:e[sd]?|ing)|"
            r"lower(?:s|ed|ing)?|cuts?|reaffirm(?:s|ed|ing)?|withdraw(?:s|n|ing)?|updated)\b",
            text,
            re.I,
        )
        if external_expectation and not explicit_issuer_action:
            return False
    if rule.concept in {"earnings.performance", "financial.operating_performance"}:
        projected = re.search(r"\b(?:forecast|guidance|project(?:s|ed)?|estimate[sd]?|anticipates?|expects?|sees|reaffirm(?:s|ed|ing)?|is looking for|potential|could|may)\b", text, re.I)
        observed = re.search(r"\b(?:reported|actual|trailing[- ]twelve[- ]month|TTM|beat|miss(?:ed|es)?|better[- ]than[- ]expected|weaker[- ]than[- ]expected|rose|fell|grew|declined|slipped|climbed|increased|decreased|recovered|record)\b", text, re.I)
        if projected and not observed:
            return False
    if rule.concept == "commercial.demand_condition" and re.search(r"\bin order to\b", text, re.I):
        # "Increase X in order to Y" contains neither an order event nor order
        # demand. Remove the purpose idiom and require the demand rule to still
        # match substantive commercial language in the remaining sentence.
        if not rule.pattern.search(re.sub(r"\bin order to\b", "", text, flags=re.I)):
            return False
    if rule.concept == "capital.financing" and re.search(r"\bmarket price per share\b", text, re.I):
        if not re.search(
            r"\b(?:offering|placement|financing|convertible|notes?|bonds?|shelf|"
            r"sale of (?:common|preferred) stock|investment from)\b",
            text,
            re.I,
        ):
            return False
    if rule.concept == "earnings.release_schedule":
        analyst_prediction = re.search(r"\b(?:analysts?|the analyst|argued|expects?|forecasts?|projects?)\b", text, re.I)
        concrete_time = re.search(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|today|tomorrow|before the bell|after (?:the )?bell|market close|market hours|\w+ \d{1,2})\b", text, re.I)
        if analyst_prediction and not concrete_time:
            return False
    if rule.concept == "analyst.issuer_assessment" and re.search(
        r"\b(?:upgrad\w*|downgrad\w*|maintain\w*|reiterat\w*|initi(?:ate|ates|ated|ating)|"
        r"rating|from\s+(?:positive|negative|buy|sell|neutral|outperform|underperform|overweight|underweight)\s+to)\b",
        text,
        re.I,
    ):
        # Rating endpoints belong to analyst.rating_action. Treating words such
        # as "positive" in "downgraded from positive to neutral" as a separate
        # opinion reverses or dilutes the action itself.
        return False
    if rule.concept == "market.price_move_observed" and re.search(r"\b(?:dollar index|currency|forex|euro|yen|yuan|pound sterling)\b", text, re.I):
        return False
    return True


def _time_relation(text: str, kind: str) -> str:
    if kind == "forecast" or re.search(r"\b(?:will|expects?|next year|future)\b", text, re.I): return "forward"
    if re.search(r"\b(?:previously|last (?:year|quarter|month)|historically)\b", text, re.I): return "historical"
    return "current"


_ADVERSE_REGULATORY_RESPONSE_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\btitle:\s*[^\n]*\b(?:complete response letters?|crls?)\b",
    r"\b(?:issue|issued|issuing|receiv(?:e[sd]?|ing)|send(?:s|ing)?|sent|confirm(?:s|ed|ing)?)\b"
    r".{0,100}\b(?:complete response letters?|crls?)\b",
    r"\b(?:complete response letters?|crls?)\b.{0,100}"
    r"\b(?:issue|issued|issuing|receiv(?:e[sd]?|ing)|(?:was |were )?sent)\b",
    r"\b(?:refus(?:e[sd]?|al) to file|refuse-to-file)\b",
    r"\b(?:plac(?:e[sd]?|ing)|impos(?:e[sd]?|ing)|remain(?:s|ed|ing)?|(?:is|was|were))\b"
    r".{0,80}\b(?:on (?:a )?)?clinical hold\b",
    r"\b(?:fda|ema)\b.{0,140}\b(?:did not approve|has not approved|"
    r"declin(?:e[sd]?|ing) (?:the )?approval|den(?:y|ies|ied|ial) (?:the )?approval|"
    r"reject(?:s|ed|ing) (?:the )?(?:application|submission))\b",
))


def _is_adverse_regulatory_response(normalized_text: str) -> bool:
    """Identify regulator decisions that block or delay the requested authorization."""
    return any(pattern.search(normalized_text) for pattern in _ADVERSE_REGULATORY_RESPONSE_PATTERNS)


def _is_resolved_clinical_hold(normalized_text: str) -> bool:
    return bool(re.search(
        r"\b(?:lift(?:s|ed|ing)?|remov(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))\b"
        r".{0,80}\bclinical hold\b|\bclinical hold\b.{0,80}"
        r"\b(?:lift(?:s|ed|ing)?|remov(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))\b",
        normalized_text,
    ))


def _sentiment(
    text: str,
    rule: ConceptRule,
    role: str,
    typed_facts: Sequence[Mapping[str, Any]] = (),
    *,
    entity: Mapping[str, Any] | None = None,
    mention_terms: Sequence[str] = (),
) -> tuple[str, int]:
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
    if rule.concept == "index.membership" and entity is not None:
        direction = _index_membership_direction(normalized, entity, mention_terms)
        if direction == "addition":
            return "positive", 2
        if direction == "removal":
            return "negative", 2
    if rule.concept == "guidance.issued":
        relations = {
            str(fact.get("relation"))
            for fact in typed_facts
            if fact.get("fact_type") == "estimate_comparison"
            and fact.get("subject_role") == "issuer_guidance"
            and fact.get("comparator_role") == "consensus_estimate"
        }
        if "below" in relations and "above" not in relations:
            return "negative", 3
        if "above" in relations and "below" not in relations:
            return "positive", 3
        if relations:
            return "neutral", 0
    if rule.concept in {"clinical.regulatory_milestone", "regulatory.action"}:
        # A regulator's adverse disposition is the controlling event even when
        # the same sentence names the approval being sought. Remediation scope,
        # management confidence, and approvals of separate application
        # components remain independent evidence rather than canceling it.
        if _is_resolved_clinical_hold(normalized):
            return "positive", 3
        if _is_adverse_regulatory_response(normalized):
            return "negative", 4
    if rule.concept in {"earnings.performance", "financial.operating_performance"}:
        if re.search(r"\b(?:narrowed|reduced|cut)\b.{0,40}\b(?:net )?loss\b|\b(?:net )?loss\b.{0,40}\bnarrowed\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:widened|increased)\b.{0,40}\b(?:net )?loss\b|\b(?:net )?loss\b.{0,40}\bwidened\b", normalized):
            return "negative", 2
    if rule.concept == "listing.market_structure":
        if re.search(
            r"\b(?:gets?|got|receiv(?:e[sd]?|ing)|receipt|grant(?:s|ed|ing)|extension)\b"
            r".{0,140}\b(?:extension|additional \d+ (?:calendar )?day period)\b"
            r".{0,140}\b(?:regain compliance|minimum bid)",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:to|will|seeks? to|aims? to|expects? to|in order to)\s+regain compliance\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\bnot (?:yet )?regain(?:ed)? compliance\b|\b(?:delisting|non[- ]compliance)\b|"
            r"\b(?:no longer|fail(?:s|ed)? to) meet(?:s)?\b.{0,50}\b(?:minimum bid|listing requirement)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(r"\b(?:has |have |had )?regained compliance\b|\bregains compliance\b", normalized):
            return "positive", 2
        reverse_split = r"(?:reverse(?:\s+(?:stock|share))?\s+split|share consolidation)"
        reverse_split_action = (
            r"(?:announc\w*|approv\w*|authoriz\w*|implement\w*|complet\w*|effectuat\w*|"
            r"takes? effect|will become effective|will reduce)"
        )
        if re.search(
            rf"\btitle:\s*[^\n]*\b{reverse_split}\b|"
            rf"\b{reverse_split_action}\b.{{0,140}}\b{reverse_split}\b|"
            rf"\b{reverse_split}\b.{{0,140}}\b{reverse_split_action}\b",
            normalized,
        ):
            return "negative", 3
    if rule.concept == "commercial.contract":
        if re.search(
            r"\b(?:contract|agreement)\s+(?:termination|cancellation|non[- ]renewal)\b|"
            r"\b(?:termination|cancellation|non[- ]renewal)\b.{0,80}\b(?:contract|agreement)\b|"
            r"\b(?:contract|agreement)\b.{0,80}\b(?:terminated|cancelled|canceled|not renewed)\b",
            normalized,
        ):
            return "negative", 4
    if rule.concept == "commercial.partnership":
        if re.search(
            r"\b(?:terminat\w*|cancel\w*|non[- ]renew\w*|end(?:s|ed|ing)?)\b.{0,100}\b(?:partnership|collaboration|alliance|joint venture)\b|"
            r"\b(?:partnership|collaboration|alliance|joint venture)\b.{0,100}\b(?:terminat\w*|cancel\w*|non[- ]renew\w*|end(?:s|ed|ing)?)\b|"
            r"\b(?:surprised|disappointed)\b.{0,120}\b(?:partnership|collaboration|alliance|joint venture)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(r"\bin (?:19|20)\d{2}\b|\bpreviously\b|\bhistorically\b", normalized):
            return "neutral", 0
        if re.search(
            r"\b(?:announc\w*|begin\w*|enter\w*|establish\w*|expand\w*|extend\w*|form\w*|launch\w*|renew\w*|sign\w*)\b.{0,100}\b(?:partnership|collaboration|alliance|joint venture)\b|"
            r"\b(?:partnership|collaboration|alliance|joint venture)\b.{0,100}\b(?:announc\w*|begin\w*|enter\w*|establish\w*|expand\w*|extend\w*|form\w*|launch\w*|renew\w*|sign\w*)\b",
            normalized,
        ):
            return "positive", 2
        return "neutral", 0
    if rule.concept == "operations.cost_efficiency" and re.search(
        r"\b(?:adapt operations?|associated with (?:the |a )?(?:lost )?customer|"
        r"contract (?:loss|termination|cancellation|non[- ]renewal)|customer loss|"
        r"lost (?:a )?(?:major )?customer|mitigat\w* (?:the )?impact|"
        r"restructur\w*|workforce reduction|layoffs?|job cuts?)\b",
        normalized,
    ):
        return "neutral", 0
    if rule.concept in {"operations.business_update", "operations.workforce"}:
        if re.search(r"\b(?:layoffs?|laid off|job cuts?|workforce reduction|restructur\w*)\b", normalized):
            return "negative", 3
    if rule.concept == "corporate_transaction.acquisition":
        if role == "target": return "positive", 3
        if re.search(r"\b(?:dilutive|difficult|struggle|overpay|debt burden|reject\w*|not in (?:the )?best interest)\b", normalized): return "negative", 3
        if re.search(r"\b(?:accretive|synerg|complementary|strategic fit|fund(?:s|ed|ing)? (?:a |an |the |its )?(?:pending )?acquisition|increase\w* (?:the )?(?:company'?s )?(?:operational )?scale|expand\w* (?:the )?(?:company'?s )?(?:scale|footprint)|high[- ]value (?:development )?inventory)\w*\b", normalized): return "positive", 2
        if re.search(r"\b(?:will combine|amalgamat\w*|complet(?:e|es|ed|ion) of the (?:merger|combination))\b", normalized): return "positive", 2
        return "neutral", 0
    if rule.concept == "governance.shareholder_vote":
        if re.search(r"\breject\w*\b.{0,80}\b(?:ban|restrict|audit)\b", normalized):
            return "positive", 2
        return "neutral", 0
    positive = _sentiment_cue_count(rule.positive, normalized)
    negative = _sentiment_cue_count(rule.negative, normalized)
    if positive > negative: return "positive", min(4, 1 + positive)
    if negative > positive: return "negative", min(4, 1 + negative)
    return "neutral", 0


def _index_membership_direction(
    normalized_text: str,
    entity: Mapping[str, Any],
    mention_terms: Sequence[str],
) -> str | None:
    aliases = {
        alias
        for value in (*mention_terms, str(entity.get("display_name") or ""), str(entity.get("ticker") or ""))
        if (alias := _normalize_alias(value))
        and (_safe_alias(alias) or alias == _normalize_alias(entity.get("ticker") or ""))
    }
    if not aliases:
        return None
    entity_pattern = "(?:" + "|".join(
        re.escape(alias).replace(r"\ ", r"\s+")
        for alias in sorted(aliases, key=len, reverse=True)
    ) + ")"
    subject = rf"\b{entity_pattern}\b"
    index = r"\b(?:index|s p|russell|nasdaq 100)\b"
    if re.search(rf"\breplaced by\b.{{0,100}}{subject}", normalized_text):
        return "addition"
    if re.search(rf"{subject}.{{0,100}}\b(?:to be|will be|is|was) replaced by\b", normalized_text):
        return "removal"
    if re.search(rf"\breplac(?:e|es|ed|ing)\b.{{0,100}}{subject}", normalized_text):
        return "removal"
    if re.search(
        rf"{subject}.{{0,140}}\b(?:to join|join(?:s|ed)?|added to|included in)\b.{{0,100}}{index}|"
        rf"{subject}.{{0,140}}\breplac(?:e|es|ed|ing)\b",
        normalized_text,
    ):
        return "addition"
    if re.search(
        rf"{subject}.{{0,140}}\b(?:removed from|deleted from|leaves?|exits?)\b.{{0,100}}{index}",
        normalized_text,
    ):
        return "removal"
    return None


def _sentiment_term_present(term: str, normalized_text: str) -> bool:
    """Match a complete cue and its grammatical forms, never an inner substring."""
    return bool(_sentiment_cue_spans(term, normalized_text))


_POLARITY_WORD_FAMILIES = tuple(re.compile(pattern) for pattern in (
    r"accept(?:s|ed|ing|ance)?", r"affirm(?:s|ed|ing|ation)?",
    r"approv(?:e|es|ed|ing|al|als)",
    r"authoriz(?:e|es|ed|ing|ation|ations)", r"award(?:s|ed|ing)?",
    r"cancel(?:s|ed|ing|ation|ations|led|ling|lation|lations)?", r"challeng(?:e|es|ed|ing)",
    r"clos(?:e|es|ed|ing)", r"commercializ(?:e|es|ed|ing|ation)",
    r"complet(?:e|es|ed|ing|ion|ions)", r"convert(?:s|ed|ing|ible)?",
    r"creat(?:e|es|ed|ing|ion)", r"declin(?:e|es|ed|ing)",
    r"decreas(?:e|es|ed|ing)", r"deleverag(?:e|es|ed|ing)",
    r"deteriorat(?:e|es|ed|ing|ion)", r"discontinu(?:e|es|ed|ing|ation)",
    r"downgrad(?:e|es|ed|ing)", r"dropp?(?:s|ed|ing)?",
    r"(?:expand(?:s|ed|ing)?|expansion)", r"gain(?:s|ed|ing)?",
    r"grant(?:s|ed|ing)?", r"hir(?:e|es|ed|ing)",
    r"improv(?:e|es|ed|ing|ement|ements)", r"increas(?:e|es|ed|ing)",
    r"introduc(?:e|es|ed|ing|tion)", r"jump(?:s|ed|ing)?",
    r"launch(?:es|ed|ing)?", r"lower(?:s|ed|ing)?",
    r"miss(?:es|ed|ing)?", r"open(?:s|ed|ing)?", r"order(?:s|ed|ing)?",
    r"rais(?:e|es|ed|ing)", r"reaffirm(?:s|ed|ing|ation)?",
    r"recall(?:s|ed|ing)?", r"receiv(?:e|es|ed|ing)",
    r"reduc(?:e|es|ed|ing|tion|tions)", r"refinanc(?:e|es|ed|ing)",
    r"regain(?:s|ed|ing)?", r"reject(?:s|ed|ing|ion)?",
    r"renew(?:s|ed|ing|al)?", r"repay(?:s|ed|ing|ment)?",
    r"repurchas(?:e|es|ed|ing)", r"resign(?:s|ed|ing|ation)?",
    r"restat(?:e|es|ed|ing|ement)", r"restructur(?:e|es|ed|ing)",
    r"secur(?:e|es|ed|ing)", r"sign(?:s|ed|ing)?",
    r"slipp?(?:s|ed|ing)?", r"slump(?:s|ed|ing)?",
    r"spik(?:e|es|ed|ing)", r"surg(?:e|es|ed|ing)",
    r"(?:suspend(?:s|ed|ing)?|suspension)", r"terminat(?:e|es|ed|ing|ion|ions)",
    r"upgrad(?:e|es|ed|ing)", r"weakness(?:es)?",
    r"withdraw(?:s|n|ing)?|withdrew",
    r"(?:beat|beats|beaten|beating)", r"(?:fall|falls|falling|fell|fallen)",
    r"(?:grow|grows|growing|grew|grown|growth)", r"(?:lose|loses|losing|lost)",
    r"(?:rise|rises|rising|rose|risen)", r"(?:sell|sells|selling|sold)",
    r"(?:win|wins|winning|won)",
))

_POLARITY_PREFIX_STEMS = frozenset({
    "amalgamat", "authoriz", "bankrupt", "challeng", "commercializ",
    "declin", "decreas", "deleverag", "deterior", "improv", "increas",
    "insolven", "introduc", "profitab", "refinanc", "repurchas", "restructur",
})


def _sentiment_cue_count(terms: Sequence[str], normalized_text: str) -> int:
    text = _normalize_alias(normalized_text)
    matched_families = {
        pattern.pattern
        for term in terms
        for pattern in (_sentiment_cue_pattern(term),)
        if pattern.search(text)
    }
    return len(matched_families)


def _sentiment_cue_spans(term: str, normalized_text: str) -> set[tuple[int, int]]:
    text = _normalize_alias(normalized_text)
    pattern = _sentiment_cue_pattern(term)
    return {match.span() for match in pattern.finditer(text)}


def _sentiment_cue_pattern(term: str) -> re.Pattern[str]:
    cue = _normalize_alias(term)
    token_patterns = [_polarity_token_pattern(token) for token in cue.split()]
    return re.compile(r"\b" + r"\s+".join(token_patterns) + r"\b")


def _polarity_token_pattern(token: str) -> str:
    if token == "advantage":
        return r"(?<!dis)advantage"
    for family in _POLARITY_WORD_FAMILIES:
        if family.fullmatch(token):
            return f"(?:{family.pattern})"
    if token in _POLARITY_PREFIX_STEMS:
        return re.escape(token) + r"[a-z]*"
    return re.escape(token)


def _entity_in_quote(
    entity: Mapping[str, Any],
    quote: str,
    mention_terms: Sequence[str] = (),
) -> bool:
    ticker = re.escape(str(entity.get("ticker") or ""))
    if ticker and re.search(rf"(?<![A-Z0-9])\$?{ticker}(?![A-Z0-9])", quote, re.I):
        return True
    normalized_quote = f" {_normalize_alias(quote)} "
    terms = (*mention_terms, str(entity.get("display_name") or ""))
    return any(
        _safe_alias(alias := _normalize_alias(term)) and f" {alias} " in normalized_quote
        for term in terms
    )


def _entities_for_quote(
    entities: Sequence[Mapping[str, Any]],
    quote: str,
    previous_entity_ids: Sequence[str],
    mention_terms: Mapping[str, Sequence[str]],
    *,
    inherit_subject: bool = False,
) -> list[Mapping[str, Any]]:
    explicit = [
        row
        for row in entities
        if _entity_in_quote(
            row,
            quote,
            mention_terms.get(str(row["entity_id"]), ()),
        )
    ]
    if explicit:
        return explicit
    if not inherit_subject and not re.search(r"\b(?:the company|it|its|management|the board|shares?|stock)\b", quote, re.I):
        return []
    if len(entities) == 1:
        return [entities[0]]
    previous = set(previous_entity_ids)
    inherited = [row for row in entities if str(row["entity_id"]) in previous]
    if inherit_subject and inherited:
        return inherited
    return inherited if len(inherited) == 1 else []


def _issuer_scoped_concept(concept: str) -> bool:
    return concept.startswith((
        "analyst.", "capital.", "clinical.", "commercial.", "corporate_transaction.",
        "credit.", "earnings.", "estimate.", "financial.", "governance.", "guidance.",
        "legal.", "listing.", "operations.", "ownership.", "product.", "regulatory.",
        "strategy.", "technology.",
    ))


def _has_issuer_scoped_rule(
    text: str,
    rules: Sequence[ConceptRule],
) -> bool:
    return any(
        _issuer_scoped_concept(rule.concept)
        and rule.pattern.search(text)
        and _rule_applicable(rule, text)
        for rule in rules
    )


def _boilerplate_sentence(text: str) -> bool:
    """Reject navigation and ingestion metadata without altering authoritative source offsets."""
    value = text.lstrip(" -\t")
    return bool(re.match(
        r"(?:related (?:links?|news|articles?)|also read|read more|continue reading|see also|source \[(?:external|provider_body)|"
        r"image:|disclaimer:|click here|enter a symbol|analyze any stock|free stock analysis|to track all upcoming earnings)\b",
        value,
        re.I,
    ))


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    """Return exact source spans while preserving decimals and common abbreviations."""
    abbreviations = {"inc", "corp", "co", "ltd", "llc", "plc", "mr", "mrs", "ms", "dr", "st", "vs", "est", "adj", "u.s", "a.m", "p.m"}
    spans: list[tuple[int, int, str]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        boundary = char in "!?;\n"
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
def _alias_variants(value: str) -> tuple[str, ...]:
    normalized = _normalize_alias(value)
    variants = {normalized}
    # Generate grammatical and corporate-name variants compositionally. This
    # covers combinations such as "The Alpha" and "Alpha Co" without
    # issuer-specific exceptions. Candidate scoping still guards ambiguous
    # one-word aliases.
    pending = [normalized]
    while pending:
        candidate = pending.pop()
        derived = {
            re.sub(r"^the\s+", "", candidate).strip(),
            re.sub(
                r"\s+(?:incorporated|inc|corporation|corp|company|co|limited|ltd|plc)$",
                "",
                candidate,
            ).strip(),
            re.sub(r"\bpharmaceuticals?\b", "pharma", candidate).strip(),
        }
        for variant in derived - variants - {""}:
            variants.add(variant)
            pending.append(variant)
    return tuple(sorted(variants))
def _safe_alias(value: str) -> bool:
    return len(value) >= 5 and value not in {
        "america", "american", "block", "capital", "company", "credit",
        "discover", "early", "element", "energy", "equity", "financial",
        "global", "group", "guidance", "holdings", "international",
        "investors", "mining", "national", "ordinary shares", "performance",
        "pharmaceuticals", "public", "securities", "solutions", "standard",
        "strategy", "target", "technology", "the company", "trading", "united",
        "western",
    }
def _normalize_ticker_identifier(value: Any) -> str:
    return EXCHANGE_PREFIX_RE.sub("", str(value or "").upper().strip())
def _as_date(value: str) -> date | None:
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try: return date.fromisoformat(value[:10])
        except ValueError: return None

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import CONTRACT_VERSION, PRODUCTION_VERSION, validate_document
from .provider_context import classify_provider_context
from .facts import extract_regulatory_decision_facts, extract_typed_facts
from .reviewed_title_policy import classify_reviewed_title_policy
from .synthesis import derive_eligibility, derive_issuer_views, derive_synthesis


ENGINE_VERSION = "news_synthesis_engine_v57"
EXCHANGE_TICKER_RE = re.compile(
    r"\b(?P<exchange>NASDAQ|NYSE|NYSE\s+AMERICAN|NYSEAMERICAN|AMEX|"
    r"OTC(?:QX|QB)?|TSX|TSXV|CSE)\s*[:\-]\s*"
    r"(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\b",
    re.I,
)
CASHTAG_RE = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9.\-]{0,9})\b")
ROUNDUP_RE = re.compile(r"\b(?:stocks?|companies|biggest movers?|gainers?|losers?)\s+(?:moving|to watch)|\bmarket\s+(?:wrap|recap|update)\b", re.I)
WHY_MOVING_RE = re.compile(r"\bwhy\s+(?:is|are|did)\b.*\b(?:stock|shares?)\b.*\bmov", re.I)
WHY_MOVING_TITLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "why_or_what_price_move_question",
        re.compile(
            r"\b(?:why\s+(?:is|are|did|has|have|was|were|does|do)|"
            r"what(?:'s| is)\s+going\s+on\s+with)\b.{0,220}\b(?:stock|shares?)\b",
            re.I,
        ),
    ),
    (
        "shares_trading_direction",
        re.compile(
            r"\bshares?\b.{0,120}\b(?:(?:are|is|were|was)\s+)?"
            r"trading\s+(?:higher|lower|up|down)\b",
            re.I,
        ),
    ),
    (
        "stock_or_shares_price_action",
        re.compile(
            r"\b(?:stock|shares?)\b(?!\s+(?:could|may|might|will|would|can|should|to)\b)"
            r".{0,32}\b(?:jumps?|jumped|jumping|surges?|surged|surging|soars?|soared|"
            r"soaring|rall(?:ies|ied|ying)|rises?|rose|rising|"
            r"gains?|gained|gaining|pops?|popped|popping|spikes?|spiked|spiking|"
            r"climbs?|climbed|climbing|rockets?|rocketed|rocketing|skyrockets?|"
            r"skyrocketed|skyrocketing|slides?|slid|sliding|slumps?|slumped|slumping|"
            r"sinks?|sank|sinking|falls?|fell|falling|drops?|dropped|dropping|"
            r"tumbles?|tumbled|tumbling|plunges?|plunged|plunging|dips?|dipped|dipping|"
            r"pauses?|paused)\b(?=.{0,120}(?:\b(?:after(?:\s+hours)?|following|on|as|"
            r"amid|because|today|premarket|postmarket|monday|tuesday|wednesday|thursday|"
            r"friday|here's\s+why|what(?:'s|\s+is)\s+(?:driving|happening)|"
            r"why\s+it\s+is\s+trending)\b|\d+(?:\.\d+)?%))",
            re.I,
        ),
    ),
    (
        "movement_explanation_prompt",
        re.compile(
            r"\b(?:what(?:'s| is)\s+going\s+on|"
            r"what(?:'s| is)\s+behind\s+(?:the\s+)?(?:move|jump|drop|rise|rally)|"
            r"what(?:'s| is)\s+up\s+with\s+the\s+(?:move|jump|drop|rise|rally|surge))\b",
            re.I,
        ),
    ),
)
EARNINGS_CALL_TITLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "transcript",
        re.compile(
            r"(?:\b(?:earnings|conference)\s+(?:conference\s+)?call\b.{0,160}\btranscript\b|"
            r"\btranscript\b.{0,160}\b(?:earnings|conference)\s+(?:conference\s+)?call\b)",
            re.I,
        ),
    ),
    (
        "call_quote",
        re.compile(
            r"\bduring\s+(?:the\s+)?(?:conf(?:erence)?\.?|earnings)\s+call\b",
            re.I,
        ),
    ),
)
ANALYST_RE = re.compile(r"\b(?:analyst|price target|rating|upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?)\b", re.I)
ATTRIBUTED_ASSESSMENT_RE = re.compile(
    r"\b(?:short|bear|bull)\s+thesis\b|"
    r"\b(?:research|short seller)\b.{0,180}\b(?:accus\w*|scam\w*|fraud\w*|challenge\w*)\b|"
    r"\b(?:out\s+)?in\s+defen[cs]e\s+of\b|"
    r"\b[A-Z][A-Za-z&.-]+'s\s+[A-Z][A-Za-z'.-]+\s+"
    r"(?:tells?|writes?|publishes?|discuss(?:es|ing))\b|"
    r"\b[A-Z][A-Za-z&.-]+(?:\s+[A-Z][A-Za-z&.-]+){1,3}\s+"
    r"(?:publishes?|writes?)\b.{0,80}\b(?:evidence|thesis|view|case)\b",
    re.I,
)
REGULATORY_RE = re.compile(r"\b(?:SEC|FDA|regulator|regulatory|trading halt|compliance notice|Nasdaq (?:comments|halts|notifies|determines)|NYSE (?:comments|halts|notifies|determines))\b", re.I)
AUTOMATED_RE = re.compile(r"\b(?:automated market statistics|benzinga pro data|generated automatically)\b", re.I)
EXCHANGE_PREFIX_RE = re.compile(
    r"^(?:NASDAQ|NYSE(?:\s+AMERICAN)?|NYSEAMERICAN|AMEX|OTC(?:QX|QB)?)\s*[:\-]\s*",
    re.I,
)
CANADIAN_EXCHANGE_PREFIX_RE = re.compile(
    r"^(?P<exchange>TSX|TSXV|CSE)\s*[:\-]\s*(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})$",
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


def why_moving_title_pattern(title: str) -> str | None:
    """Return the reviewed price-reaction headline family, if any."""
    for name, pattern in WHY_MOVING_TITLE_PATTERNS:
        if pattern.search(title):
            return name
    return None


def earnings_call_title_pattern(title: str) -> str | None:
    """Return the reviewed earnings-call material family, if any."""
    for name, pattern in EARNINGS_CALL_TITLE_PATTERNS:
        if pattern.search(title):
            return name
    return None


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
        explicit = {
            _exchange_qualified_ticker_identifier(
                match.group("exchange"),
                match.group("ticker"),
            )
            for match in EXCHANGE_TICKER_RE.finditer(text)
        } | {_normalize_ticker_identifier(value) for value in CASHTAG_RE.findall(text)}
        candidate_set = {_normalize_ticker_identifier(value) for value in candidates if value}
        explicit.discard("")
        candidate_set.discard("")
        matches: dict[str, set[str]] = {ticker: {"explicit_ticker_in_text"} for ticker in explicit}
        normalized_text = f" {_normalize_alias(text)} "
        for alias, rows in self._by_alias.items():
            if f" {alias} " not in normalized_text:
                continue
            valid = [row for row in rows if row.valid_on(day)]
            tickers = {_normalize_ticker_identifier(row.ticker) for row in valid}
            preferred = tickers & candidate_set
            if preferred:
                tickers = preferred
            elif len(alias.split()) == 1:
                # A bare global brand token is too weak to establish security
                # identity: ordinary prose routinely overlaps symbols' legacy
                # aliases (for example trade, support, products, or total).
                # Exact provider candidates retain candidate-scoped brand
                # resolution, while evaluation targets are admitted later with
                # separate provenance and locally grounded participation.
                continue
            issuer_ids = {
                row.issuer_id
                for row in valid
                if _normalize_ticker_identifier(row.ticker) in tickers
            }
            if len(tickers) == 1 or len(issuer_ids) == 1:
                for ticker in tickers:
                    matches.setdefault(ticker, set()).add(f"issuer_alias:{alias}")
        # Provider candidates may use a distinctive public brand while the
        # point-in-time authority stores a longer legal name. Admit an exact
        # candidate-scoped brand mention without making the short alias a
        # global resolver key. This preserves provider scope and prevents a
        # common word from binding unrelated securities.
        for ticker in candidate_set:
            valid = [row for row in self._by_ticker.get(ticker, ()) if row.valid_on(day)]
            if len({row.issuer_id for row in valid}) != 1:
                continue
            candidate_aliases = {
                alias
                for row in valid
                for alias in _candidate_scoped_alias_variants(
                    (row.display_name, *row.aliases)
                )
            }
            mentioned = sorted(
                alias for alias in candidate_aliases if f" {alias} " in normalized_text
            )
            if mentioned:
                matches.setdefault(ticker, set()).add(
                    f"candidate_alias:{max(mentioned, key=len)}"
                )
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

    def supported_evaluation_targets(
        self,
        *,
        candidates: Sequence[str],
        timestamp: str,
    ) -> list[dict[str, Any]]:
        """Resolve scoring targets without representing them as provider evidence."""
        entities = self.supported_candidates(candidates=candidates, timestamp=timestamp)
        for entity in entities:
            entity["identity_evidence"] = ["evaluation_target_only"]
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
            terms.extend(
                alias
                for alias in _candidate_scoped_alias_variants(
                    (row.display_name, *row.aliases)
                )
            )
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
    _rule("analyst.rating_action", r"\b(?:upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?|rates?|ratings?)\b(?:.{0,100})\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|perform|equal[- ]weight|sector perform|market perform|rating)\b|\b(?:series|round) of downgrades\b|\banalysts?\b.{0,80}\b(?:upgrade[sd]?|downgrade[sd]?)\b|\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|equal[- ]weight|sector perform|market perform)\s+rating\b|\((?:(?:NASDAQ|NYSE|AMEX|TSX|TSXV)\s*:\s*)?[A-Z][A-Z0-9.\-]{0,9}\)\s+\$?\d+(?:\.\d+)?\s+(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|market perform)\b|\banalysts? (?:have )?(?:provided|published|offered).{0,60}ratings?\b|\b(?:bullish|somewhat bullish|indifferent|somewhat bearish|bearish)\s*=\s*\d+", "assessment", positive=("upgrade", "buy", "outperform", "overweight"), negative=("downgrade", "sell", "underperform", "underweight")),
    _rule("analyst.price_target_action", r"\b(?:price target|target price|price objective|PT|PO|P/T|\$\d+(?:\.\d+)? target|target on)\b", "forecast", positive=("raises", "raised", "higher", "increases"), negative=("cuts", "cut", "lowers", "lowered")),
    _rule("earnings.performance", r"\b(?:earnings|EPS|revenues?|sales|net income|profit|quarterly results?|financial results?)\b.{0,180}\b(?:reports?|reported|beat[sd]?|miss(?:es|ed)?|above|below|better[- ]than[- ]expected|stronger[- ]than[- ]expected|weaker[- ]than[- ]expected|rose|rise|surge[sd]?|gain(?:s|ed)?|fell|drop(?:s|ped)?|declin(?:e|ed)|grew|increase[sd]?|decrease[sd]?|loss|up(?: from)?|down(?: from)?|narrowed|widened|inline)\b|\b(?:reports?|reported|posts?|beat[sd]?|miss(?:es|ed)?|rose|rise|surge[sd]?|gain(?:s|ed)?|fell|drop(?:s|ped)?|grew|narrowed|widened|disappointing|swings? to)\b.{0,100}\b(?:earnings|EPS|revenues?|sales|profit|results?|losses?)\b|\bposts?\b.{0,40}\b(?:F?Q[1-4]|quarter(?:ly)?)\b.{0,40}\b(?:beat|miss)\b|\b(?:better|stronger|weaker)[- ]than[- ]expected results?\b|\b(?:EPS|earnings per share)\b.{0,30}\$?\s*\(\d+(?:\.\d+)?\)", positive=("beat", "above", "better-than-expected", "stronger-than-expected", "grew", "rose", "rise", "surge", "gain", "record", "increase", "up from", "narrowed", "swings to profit"), negative=("miss", "below", "weaker-than-expected", "fell", "drop", "decline", "decrease", "loss", "down", "widened", "disappointing")),
    _rule(
        "earnings.performance",
        r"\b(?:EPS|earnings per share|revenues?|sales)\b\s+"
        r"(?:was|were)\s+.{0,50}\b(?:vs\.?|versus)\s+"
        r"(?:analysts?'?\s+)?(?:estimates?|consensus)\b",
        "event",
    ),
    _rule("guidance.issued", r"\b(?:issues?|provid(?:e|es|ed)|guid(?:e|es|ed)|rais(?:e[sd]?|ing)|boost(?:s|ed|ing)?|increas(?:e[sd]?|ing)|lower(?:s|ed)?|cuts?|reaffirm(?:s|ed|ing)?|withdraws?|updates?)\b.{0,100}\b(?:guidance|outlook|forecast|rev\.?|revenue|sales|earnings|EPS|EBITDA|growth|margin)\b|\b(?:not providing|declines? to provide|will not provide|withholds?)\b.{0,80}\bguidance\b|\b(?:guidance|outlook)\b.{0,100}\b(?:raised|boosted|increased|lowered|cut|reaffirmed|withdrawn|unchanged|expects?|projection|projecting)\b|\b(?:sees?|saw|expects?|anticipates?|project(?:s|ed|ing)?|is looking for)\b.{0,120}\b(?:rev\.?|revenue|sales|earnings|EPS|EBITDA|growth|margin|comparable[- ]store sales|same[- ]store sales|comps?|comp)\b|\bexpected to\s+(?:comp|report comparable[- ]store sales|report same[- ]store sales)\b|\b(?:rev\.?|revenue|sales|earnings|EPS|EBITDA|growth|margin|free cash flow)\b.{0,160}\bprojection\s*=|\b(?:profit|EPS|earnings|rev\.?|revenue|sales)\s+(?:forecast|outlook|guidance)\b.{0,160}\b(?:fell short|below|miss(?:es|ed)?|above|beat[sd]?)\b.{0,80}\b(?:street|consensus|analysts?'? estimates?|view)\b|\b(?:profit|EPS|earnings|rev\.?|revenue|sales)\s+(?:forecast|outlook|guidance)\b.{0,120}\bwhile\s+(?:analysts?'? estimates?|consensus)\b", "forecast", positive=("raise", "boost", "increas", "higher"), negative=("cut", "lower", "withdraw", "reduce", "weaker", "not providing", "declines to provide", "withhold")),
    _rule(
        "guidance.issued",
        r"\b(?:guidance|outlook|forecast)\b.{0,180}"
        r"\b(?:EPS|earnings|loss|revenue|sales|EBITDA|margin)\b.{0,160}"
        r"\b(?:consensus|estimate|expectations?|street|short of|below|above|versus|vs\.?)\b|"
        r"\bforecast(?:s|ed|ing)?\b.{0,80}\bloss\b",
        "forecast",
        positive=("above", "beat"),
        negative=("below", "short of", "weak", "lower", "loss"),
    ),
    _rule("corporate_transaction.acquisition", r"\b(?:acquir(?:e|es|ed|ing)|acquisitions?|merger|takeover)\b|\b(?:will|would|to)\s+buys?\b|\bwill\s+be\s+purchased\s+by\b|\bbuys?\b.{1,100}\bfor\s+\$|\bpurchase(?:s|d)? of .{0,100}\b(?:assets?|business|operations?)\b|\b(?:rumored?|possible|potential)?\s*bid for\b|\b(?:firm|binding|cash|takeover) offer for\b|\btakeover chatter in\b|\b(?:will|would|agrees? to) combine with\b|\bamalgamat(?:e|es|ed|ing) with\b|\b(?:complet(?:e|es|ed|ion) of|proposed) (?:the )?(?:business )?combination\b|\bproposed deal with\b", positive=("agreed", "complete", "closes", "approved", "purchase", "purchased", "will combine", "amalgamat"), negative=("terminate", "withdraw", "no longer pursue", "blocked", "reject", "not in best interest")),
    _rule("corporate_transaction.asset_sale", r"\b(?:asset sale|sale of .{0,100}(?:assets?|business|operations?|units?|properties|buildings?|towers?|facilities|portfolio)|closes? (?:the )?sale of .{0,100}(?:assets?|business|operations?|units?|properties|buildings?|towers?|facilities|portfolio)|divest(?:s|ed|iture)|spin[- ]?off|sell(?:s|ing)? (?:its )?.{0,100}(?:business|operations?|units?|properties|buildings?|towers?|facilities|portfolio))\b", positive=("complete", "closes", "proceeds", "maximize shareholder value"), negative=("distress",)),
    _rule("capital.financing", r"\b(?:public offering|registered direct offering|private placement|mixed shelf|(?:stock|share|equity|securities) shelf|shelf (?:offering|registration)|at-the-market|ATM (?:program|offering)|convertible (?:senior )?notes?|(?:convertible |senior )?notes? offering|bond offering|debt financing|equity financing|issues? .{0,60}(?:debt|notes?|bonds?)|launch(?:es|ed)? .{0,60}(?:bond|notes?|debt)|files? for .{0,80}offering|prices? .{0,80}(?:offering|shares?|ADS|ADRs?|notes?|bonds?)|register(?:s|ed|ing)? .{0,80}shares? .{0,80}issuable .{0,80}warrant exercise|(?:offer(?:s|ed)?|offering)\s+.{0,60}?\bshares?|shares? offering|offering of .{0,80}(?:shares?|notes?|units?|securities)|sale (?:by us )?of .{0,80}(?:common stock|preferred stock|debt securities|warrants)|investment from .{0,80}funds?|term sheet .{0,100}investment|conversion price .{0,40}(?:share|stock))\b", positive=("investment from",), negative=("dilution", "offering", "placement", "convertible", "shelf", "prices")),
    _rule("capital.return", r"\b(?:share repurchase|buyback|dividend|capital return)\b", positive=("restart", "increase", "raises", "special dividend", "fund capital return", "capital return"), negative=("suspend", "cut", "reduce")),
    _rule("regulatory.action", r"\b(?:trading halt|halted|resume trading|SEC action|regulatory action|compliance notice|formal investigation|clinical hold|license renewal|crackdown|advisory committee|regulator|letter of authorization|conditions? of authorization|reporting requirements?|(?:grant|grants|granted)\b.{0,100}\b(?:terrestrial|commercial|marketing|regulatory) authorization)\b|\b(?:FDA|FTC|SEC|European Commission|Nuclear Regulatory Commission|FERC|Federal Energy Regulatory Commission)\b.{0,180}\b(?:investigation|inquiry|subpoena|request(?:s|ed)? information|hold|cancel|issue|renew|order|action|notice|authoriz|reporting requirement|pre[- ]filing review)\w*\b|\benter(?:s|ed|ing)?\b.{0,80}\bpre[- ]filing review process\b.{0,120}\b(?:FERC|Federal Energy Regulatory Commission)\b|\b(?:NICE|National Institute for Health and Care Excellence|health technology assessment (?:body|agency)|payer)\b.{0,180}\b(?:reject(?:s|ed)?|does not recommend|cannot recommend|declines? to recommend|refus(?:e[sd]?|ing) coverage)\b|\b(?:reject(?:s|ed)?|does not recommend|cannot recommend|declines? to recommend|refus(?:e[sd]?|ing) coverage)\b.{0,180}\b(?:NICE|National Institute for Health and Care Excellence|health technology assessment (?:body|agency)|payer)\b", positive=("authorize", "authorization", "renew", "grant", "pre-filing review"), negative=("halt", "suspend", "noncompliance", "investigation", "inquiry", "subpoena", "crackdown", "cancel", "myocarditis", "pericarditis", "adverse", "reject", "does not recommend", "cannot recommend", "declines to recommend", "refuse")),
    _rule("clinical.regulatory_milestone", r"\b(?:FDA|EMA|SFDA|State Food and Drug Administration|NDA|BLA|USDA)\b.{0,180}\b(?:approv|reject|complete response|clinical hold|clearance|accept|authoriz|resubmission|acknowledge|submission|meeting|grant|nod|fast track|priority review|expand\w*\s+(?:the\s+)?indication)\w*\b|\b(?:grant(?:s|ed)?|agree(?:s|d)?)\b.{0,100}\b(?:meet|meeting)\b.{0,80}\bFDA\b|\b(?:plans?|intends?|expects?)\b.{0,80}\b(?:seek|file|submit)\b.{0,100}\b(?:FDA|EMA|SFDA)\b.{0,80}\b(?:priority review|sNDA|NDA|BLA|application)\b|\b(?:receiv(?:e|es|ed)|secur(?:e|es|ed))\b.{0,80}\bCE mark\b|\b(?:complete response letter|clinical hold|primary endpoint|phase [123] (?:study|trial)|letter of authorization|regulatory submission|FDA nod|SFDA approval|CE mark|fast track designation)\b", positive=("approve", "approval", "clearance", "accept", "authorize", "authorization", "grant", "nod", "CE mark", "met primary", "expands indication"), negative=("reject", "complete response", "hold", "did not meet", "missed", "myocarditis", "pericarditis", "cancel")),
    _rule("clinical.regulatory_milestone", r"\bfil(?:e[sd]?|ing)\b.{0,100}\b(?:response|submission|application)\b.{0,80}\b(?:FDA|EMA)\b|\b(?:withdraw(?:s|n|al)?|withdrew)\b.{0,100}\b(?:NDA|BLA|new drug application|application|submission)\b", positive=("file", "submitted"), negative=("withdraw", "withdrew")),
    _rule("clinical.trial_result", r"\bdata\b.{0,120}\bshows?\b.{0,100}\bbiosimilar\b.{0,80}\bhighly similar\b.{0,40}\bto\b.{0,100}", positive=("highly similar",)),
    _rule("clinical.trial_result", r"\b(?:clinical trial|study|Phase\s*[123][a-z]?(?:/[123][a-z]?)?)\b.*\b(?:endpoint|results?|data|efficacy|safety|survival|viral suppression|site)\b|\b(?:results?|data)\b.{0,120}\b(?:clinical trial|study|Phase\s*[123][a-z]?(?:/[123][a-z]?)?)\b|\b(?:initiat(?:e[sd]?|ion)|commenc(?:e[sd]?|ement)|begin(?:s|ning)?)\b.{0,100}\b(?:Phase\s*[123][a-z]?\s+)?(?:clinical\s+)?(?:study|trial)\b|\bexpansion of\b.{0,100}\bclinical development program\b.{0,120}\bnew trial\b|\bpresent(?:s|ed|ing)?\b.{0,100}\bpositive\b.{0,100}\bdata\b.{0,100}\bclinical program\b|\bpositive clinical (?:data|results?)\b|\bpublication of data\b.{0,120}\bshows?\b.{0,120}\b(?:induces?|activates?|improves?)\b.{0,140}\blinked to\b.{0,80}\b(?:prevention|treatment)\b|\b(?:met|achieved|did not meet|failed to meet|did not demonstrate)\b.{0,120}\b(?:primary\b.{0,60})?(?:endpoint|goal|dose[- ]response|statistical significance)\b|\b(?:primary|co-primary|secondary) endpoint\b.{0,100}\b(?:did not meet|failed to meet|missed)\b.{0,80}\b(?:statistical significance|endpoint)?\b|\b(?:shut(?:s|ting)? down|discontinues?|terminates?)\b.{0,100}\b(?:clinical trial|study|Phase\s*[123])\b|\b(?:investigator|physician|assessment|review)\b.{0,160}\bconclud(?:e[sd]?|ing)\b.{0,100}\bnot (?:a )?case of\b.{0,120}\brevis(?:e[sd]?|ing) (?:the )?diagnosis\b|\b(?:statistically significant(?: and clinically meaningful)? improvement|statistically significant (?:adverse |unwanted |harmful )?(?:immune|immunologic|immunological) response (?:data|results?)|significantly reduces?|prevent(?:s|ed|ing)?\s+\d+(?:\.\d+)?%\s+of|\d+(?:\.\d+)?%\s+(?:vaccine )?efficacy|\d+(?:\.\d+)?%\s+reduction in (?:the )?risk|\d+(?:\.\d+)?%\s+of patients?.{0,80}\bachieved\b|first (?:patient|subject) (?:enrolled|dosed|screened)|(?:enrolls?|doses?|screens?) (?:the )?first (?:patient|subject)|(?:opens?|initiates?|announces?)\b.{0,80}\b(?:Phase\s*[123]\s+)?trial site|high efficacy|durable viral suppression|overall survival|sustained virologic response)\b", positive=("met", "positive", "improved", "prevented", "efficacy", "trial site", "initiation", "new trial", "first patient screened"), negative=("failed", "missed", "did not meet", "shut down", "discontinue", "terminate", "adverse")),
    _rule("legal.proceeding", r"\b(?:lawsuit|litigation|investigation|subpoena|probe|targeted by (?:the )?probe|under investigation|settlement|arbitration|legal claim|security vulnerability|actively exploited vulnerability|data breach|claim for .{0,60}damages|seeking .{0,40}damages|patent peace|patent (?:is )?issued|issues? .{0,40}patent|(?:grant(?:s|ed)?|receiv(?:e|es|ed))\b.{0,80}\bpatent)\b|\bfil(?:e[sd]?|ing)\b.{0,80}\bcomplaint\b.{0,100}\bagainst\b|\b(?:sues?|sued|investigates?|investigated|charges?|charged)\b.{0,140}\b(?:discrimination|fraud|corruption|claims?|company|providers?)\b|\b(?:won|wins?)\b.{0,100}\b(?:IP rights|patent|legal)\b.{0,80}\bcase\b|\b(?:case|action|judgment)\b.{0,120}\b(?:reversed?|remanded|dismissed as moot)\b|\b(?:reverses?|remands?|dismiss(?:es|ed) as moot)\b.{0,120}\b(?:case|action|judgment)\b|\b(?:judge|court)\b.{0,140}\b(?:rules?|ruled|finds?|found|calls?|called|strikes? down|invalidates?)\b.{0,100}\b(?:restriction|limitation|ban|rule|regulation)s?\b|\b(?:judge|court)\b.{0,140}\b(?:restriction|limitation|ban|rule|regulation)s?\b.{0,100}\b(?:arbitrary|unlawful|invalid|struck down)\b", positive=("seeking damages", "served a request", "files arbitration", "patent peace", "patent issued", "issues patent"), negative=("lawsuit", "investigation", "probe", "subpoena", "breach", "vulnerability", "discriminatory", "adverse treatment")),
    _rule("listing.market_structure", r"\b(?:reverse (?:stock|share) split|reverse split|stock split|share consolidation|share combination|delist(?:s|ed|ing)?|deslit|delisting|listing compliance|listing extension|regains?\b.{0,60}\bcompliance|regained\b.{0,60}\bcompliance|regaining of\b.{0,60}\bcompliance|continued listing|non[- ]?compliance|minimum bid|late filing|failure to timely file|included in .{0,60}(?:Russell|S&P|Nasdaq).{0,20}index|IPO)\b", positive=("regained compliance", "approved listing", "included"), negative=("delist", "deslit", "noncompliance", "non-compliance", "late", "failure")),
    _rule("commercial.contract", r"\b(?:awarded|wins?|receives?|secures?|sign(?:s|ed|ing)?|enters?|affirms?)\b.{0,120}\b(?:contract|order|award|agreements?|program|initiative)\b|\bselected\b.{0,120}\bto (?:provide|deliver|supply)\b.{0,120}\b(?:services?|consultancy|supervision|supply|systems?|stations?|equipment|products?|components?)\b|\b(?:expand(?:s|ed|ing)?|extend(?:s|ed|ing)?|renew(?:s|ed|ing)?)\b.{0,100}\b(?:sales |supply |service )?agreements?\b|\b(?:deal with\b.{0,100}\b(?:provid|supply)\w*|named\b.{0,100}\bofficial\b.{0,60}\b(?:broker|provider|supplier|partner))\b|\bcontract award\b|\b(?:waiver and amendment|amendment) agreement\b|\bwaiv(?:e|es|ed|ing)\b.{0,120}\bright to terminate\b.{0,120}\b(?:fund|financ|tranche|obligation)\w*\b|\b(?:contract|agreement)\s+(?:termination|cancellation|non[- ]renewal)\b|\b(?:termination|cancellation|non[- ]renewal)\b.{0,80}\b(?:contract|agreement)\b|\b(?:follow[- ]on )?(?:contract|order|agreement|program|initiative)\b.{0,120}\b(?:awarded|affirmed|won|win|received|secured|signed|terminated|cancelled|canceled|not renewed)\b", positive=("awarded", "affirmed", "wins", "win", "received", "secured", "signed", "selected", "expand", "extend", "renew", "contract award"), negative=("cancel", "terminate", "non-renewal", "not renewed")),
    _rule("product.milestone", r"\b(?:launch|unveil|reveal|debut)\w*\b.{0,80}\bsolutions?\b|\b(?:launch|unveil|reveal|debut|showcas|commercializ|introduc|roll(?:s|ed|ing)? out|recall|discontinue|authoriz(?:e|es|ed|ing)|ships? (?:the )?first|first shipment|deliver(?:s|ed)? (?:the |its )?\d+(?:st|nd|rd|th)|release(?:s|d)? (?:the )?(?:final )?pricing|production milestone|assembly line|built? \d+)\w*\b.{0,120}\b(?:product|platform|service|(?:pilot|production|commercial) units?|feature|functionality|uploader|device|drug|treatment|vaccine|vehicle|system|game|headset|candidate|model|factory|use|panel|test|camera|dressing)\b|\bdeliver(?:s|ed)? (?:the |its )?\d+(?:st|nd|rd|th)\b|\b(?:product|feature|functionality|uploader|device|drug|treatment|vaccine|service|game|headset|vehicle|model|panel|test|camera|dressing)\b.{0,120}\b(?:launch|unveil|reveal|debut|showcas|commercializ|introduc|release|recall|discontinue|delay|authoriz|available|roll(?:s|ed|ing)? out|assembly line|first shipment)\w*\b|\b(?:products?|goods?|inventory)\b.{0,120}\breturned\b.{0,100}\b(?:company|supplier|customer|distributor|retailer|regulator)\b|\b(?:new products?|product delay|(?:lead|investigational) (?:product candidate|drug|treatment|therapy|antibody)|delivery system|bodies coming down the assembly line)\b", positive=("launch", "unveil", "reveal", "debut", "commercializ", "introduc", "release", "approval", "authorize", "available", "roll out", "rolling out", "new", "assembly", "built", "affordable", "ships the first", "first shipment", "deliver"), negative=("recall", "returned", "delay", "discontinue")),
    _rule("governance.management_change", r"\b(?:appoint(?:s|ed|ing)?|nam(?:e|es|ed|ing)|elect(?:s|ed|ing)?|resigns?|retires?|steps down|terminates?|replaces?|death|dies|died|passing)\b.{0,100}\b(?:chief executive|chief financial|CEO|CFO|president|founder|director|board)\b|\b(?:chief executive|chief financial|CEO|CFO|president|founder|director)\b.{0,80}\b(?:resigns?|retires?|steps down|appointed|named|elected|terminated|replaced|dies|died|death|passing)\b", negative=("resign", "terminated", "steps down", "death", "dies", "died", "passing")),
    _rule("corporate.communication_event", r"\b(?:releases?|publishes?|highlights?|presents?|presenting|presentation of|to present)\b.{0,120}\b(?:investor presentation|investor day|webcast|conference presentation|posters?|preclinical data)\b|\b(?:investor presentation|investor day|webcast|conference presentation|posters?|preclinical data)\b.{0,120}\b(?:releases?|publishes?|highlights?|presents?|presenting)\b"),
    _rule("operations.business_update", r"\b(?:business update|provid(?:e|es|ed|ing) (?:an? )?update on|restructur|layoff|shutdown|shut(?:s|ting)? down|ceas(?:e|es|ed|ing) operations?|expan(?:d|ds|ded|ding|sion)|job cuts?|workforce reduction|cut(?:s|ting)?\s+\d[\d,]*.{0,50}\b(?:jobs?|positions?)|service unaffected|operations? unaffected|opens? (?:an? )?(?:store|clinic|location|facility|office|dispensary)|business performance|concerns? over (?:the )?sustainability of (?:this|the) business model)\w*\b", positive=("unaffected", "opens"), negative=("layoff", "shutdown", "shut down", "cease", "restructur", "cuts", "cutting", "sustainability")),
    _rule("earnings.release_schedule", r"\b(?:will (?:report|release|post)|will be reporting|scheduled to report|set to (?:report|announce)|reports? .{0,60} on (?:Monday|Tuesday|Wednesday|Thursday|Friday)|release earnings results|release .{0,40} financial results|earnings (?:date|call beginning|release)|after (?:the )?(?:opening|closing) bell|before (?:the )?opening bell|after market (?:close|hours)|ahead of .{0,30}(?:Q[1-4]|quarterly) earnings .{0,30}(?:Monday|Tuesday|Wednesday|Thursday|Friday))\b", "reference"),
    _rule("earnings.restatement", r"\b(?:restate|restatement|should no longer be relied upon)\b", negative=("restate", "no longer be relied")),
    _rule("capital.deleveraging", r"\b(?:deleverag|(?:debt|borrowings?|term loans?|notes?|bonds?) repayment|repayment of (?:all )?(?:outstanding )?(?:debt|borrowings?|term loans?|notes?|bonds?)|repay(?:s|ed)? .*(?:debt|borrowings?|term loans?|notes?|bonds?)|reduce(?:s|d)? .*debt|cash tender offer\b.{0,160}\b(?:notes?|bonds?|debt))\w*\b", positive=("deleverag", "repay", "repayment", "reduce", "cash tender offer")),
    _rule("capital.structure", r"\b(?:authorized shares|outstanding shares|share consolidation|capital structure|refinanc\w*|repurchas\w* .{0,80}(?:notes?|bonds?|debt)|convertible bonds?)\b", positive=("refinanc", "repurchas", "extend", "later maturity")),
    _rule("credit.solvency", r"\b(?:bankrupt|chapter 11|default|going concern|substantial doubt|creditor protection|insolven|liquidity crisis|financing shortfall)\w*\b", negative=("bankrupt", "default", "going concern", "substantial doubt", "creditor protection", "insolven", "crisis", "shortfall")),
    _rule("financial.margin", r"\b(?:gross|operating|EBITDA|profit) margins?\b", positive=("expand", "improv", "increase", "accretive"), negative=("contract", "compress", "declin", "dilutive", "difficult", "struggle")),
    _rule("financial.operating_performance", r"\b(?:operating income|operating loss|OIBDA|EBITDA|profitability|net income|net loss|operating profit|results? of operations|return on (?:equity|assets)|ROE|ROA|(?:comparable|comp)(?: store)? (?:sales|net sales)|business performance|portfolio occupancy|rent collection)\b|\b(?:revenues?|sales)\b.{0,100}\b(?:rose|climbed|grew|growth|fell|slipped|declined|decreased|increased|up|down|vs\.?|compared with|year[- ]over[- ]year)\b|\b(?:occup(?:ied|ancy)|base rents? collected)\b.{0,40}\b\d+(?:\.\d+)?%", positive=("income", "profitab", "improv", "increase", "grew", "growth", "rose", "climbed", "recovered", " up ", "occupied", "collected"), negative=("loss", "declin", "deterior", "decrease", "fell", "slipped", " down ")),
    _rule("financial.cash_flow", r"\b(?:free cash flow|operating cash flow|cash burn)\b", positive=("positive", "increase", "improv"), negative=("negative", "burn", "declin")),
    _rule("financial.liquidity", r"\b(?:cash runway|liquidity|cash and equivalents|working capital|(?:enters?|entered|secures?|secured|obtains?|obtained)\b.{0,120}\b(?:revolving )?credit facility|(?:obtains?|obtained|receives?|received)\b.{0,120}\bloan\b.{0,100}\bPaycheck Protection Program|(?:obtains?|obtained|receives?|received)\b.{0,100}\bPaycheck Protection Program loan|PPP loan)\b", positive=("strong", "sufficient", "improv", "entered", "secured", "obtained", "receives", "credit facility", "PPP loan"), negative=("shortfall", "insufficient", "weak")),
    _rule("financial.loss_exposure", r"\b(?:(?:asset|goodwill|intangible|inventory|financial|accounting) impairment|impairment (?:charge|loss)|write[- ]?down|charge|loss exposure|additional losses?|catastrophe losses?)\b", negative=("impairment", "write", "charge", "loss", "catastrophe")),
    _rule("financial.internal_control", r"\b(?:material weakness|internal controls?|control deficiency)\b", negative=("weakness", "deficiency", "ineffective")),
    _rule("financial.credit_quality", r"\b(?:credit rating|credit quality|rating agency)\b", positive=("upgrade", "improv"), negative=("downgrade", "deterior")),
    _rule("financial.credit_quality", r"\b(?:card |credit-card |loan )?delinquenc(?:y|ies)\b[^,;.!?]{0,40}\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b|\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b[^,;.!?]{0,20}\b(?:card |credit-card |loan )?delinquenc(?:y|ies)\b", positive=("down", "lower", "decreas"), negative=("up", "higher", "increas"), local_evidence=True),
    _rule("financial.credit_quality", r"\b(?:credit-card |loan )?(?:write-offs?|charge-offs?)\b[^,;.!?]{0,40}\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b|\b(?:up|higher|increas\w*|down|lower|decreas\w*)\b[^,;.!?]{0,20}\b(?:credit-card |loan )?(?:write-offs?|charge-offs?)\b", positive=("down", "lower", "decreas"), negative=("up", "higher", "increas"), local_evidence=True),
    _rule("estimate.revision", r"\b(?:estimates?|consensus)\b.{0,180}\b(?:rais(?:e|ed|ing)|lower(?:ed|ing)?|revis(?:e|ed|ing)|cut|increas(?:e|ed|ing)|reduc(?:e|ed|ing)|come down|adjust(?:ed|ing))\b|\b(?:rais(?:e|ed|ing)|lower(?:ed|ing)?|revis(?:e|ed|ing)|cut|increas(?:e|ed|ing)|reduc(?:e|ed|ing)|adjust(?:ed|ing))\b.{0,180}\b(?:estimates?|consensus)\b", "forecast", positive=("raise", "higher", "increase"), negative=("lower", "cut", "reduce", "come down")),
    _rule("ownership.position_change", r"\b(?:stake|ownership|position in|shares? of)\b.{0,160}\b(?:increased|decreased|sold|bought|acquired|trimmed|exited)\b|\b(?:insider|president|director|officer|executive)\b.{0,100}\b(?:bought|purchased|sold)\b.{0,80}\bshares?\b|\b(?:increased|decreased|sold|bought|acquired|trimmed|exited)\b.{0,160}\b(?:stake|position in|shares? of)\b|\bnew\s+\d+(?:\.\d+)?%?\s+stake\b", positive=("increased", "bought", "purchased", "acquired", "new stake"), negative=("decreased", "sold", "trimmed", "exited")),
    _rule("ownership.position", r"\b(?:owns? .{0,80}(?:shares?|stake)|ownership (?:stake|interest)|beneficial owner|stake in|position in .{0,80}(?:stock|shares?|company)|activist investor stake|activist (?:investor|campaign)|activist\b.{0,80}\btarget)\b", "background"),
    _rule("commercial.partnership", r"\b(?:partnership|collaboration|strategic alliance|joint venture|partner(?:s|ed|ing)? with|named\b.{0,80}\b(?:exclusive|official)\b.{0,60}\bpartner)\b", positive=("partnership", "collaboration", "alliance", "partnering", "partnered", "named")),
    _rule("commercial.demand_condition", r"\b(?:strong|robust|growing|increas(?:e|ed|ing)|record|higher|weak|soft(?:ness)?|lukewarm|slower|lower|declin(?:e|ed|ing)|falling|delayed|pent-up)\b.{0,80}\b(?:demand|bookings|pre[- ]?orders?|orders?|deliveries|backlog|customer additions?|subscribers?|appetite)\b|\b(?:demand softness|lukewarm demand)\b|\b(?:customer|consumer|client|market) demand\b|\bdemand (?:for|from)\b|\bappetite for\b|\b(?:bookings|pre[- ]?orders?|orders?|deliveries|backlog|subscribers?)\b.{0,80}\b(?:grew|rose|increased|declined|fell|decreased|record|strong|weak)\b|\b(?:customers? lost|shortages?|client purchasing)\b|\b(?:customers?|clients?)\b.{0,100}\bsupport\b.{0,100}\b(?:efforts?|service|offering|platform|product)\b|\bsupport\b.{0,100}\befforts? to (?:offer|launch|commercialize)\b", positive=("strong", "robust", "growing", "increase", "record", "higher", "appetite", "support"), negative=("weak", "soft", "softness", "lukewarm", "declin", "slow", "lower", "lost", "shortage", "delayed", "falling")),
    _rule("commercial.competitive_position", r"\b(?:market share|competitive position|competition|competitor|competitive (?:advantage|disadvantage)|market leader|largest .{0,50}(?:company|provider|operator|producer)|first and only|well-positioned)\b", positive=("gain", "leading", "leader", "largest", "advantage", "first and only", "well-positioned"), negative=("lose", "pressure", "challeng", "disadvantage", "discriminatory")),
    _rule("operations.workforce", r"\b(?:layoffs?|job cuts?|workforce reduction|reduc(?:e[sd]?|ing)\s+(?:its\s+|the\s+)?(?:global\s+)?workforce|(?:global\s+)?workforce\s+(?:is\s+|was\s+|will be\s+)?reduc(?:e[sd]?|ing)|cut(?:s|ting)?\s+\d[\d,]*.{0,50}\b(?:jobs?|positions?)|hires?|headcount|seasonal jobs?|creat(?:e|es|ed|ing) .{0,50}jobs?|jobs? (?:created|during construction)|employs? (?:over|approximately|about|more than)?\s*\d|workforce of \d|convert(?:s|ed|ing)? .{0,50}employees? (?:into|to)|welcome back .{0,30}workers?|train .{0,30}(?:new )?(?:workers?|employees?))\b", positive=("hire", "create", "convert", "welcome back"), negative=("layoff", "cut", "reduction", "reduce")),
    _rule("operations.capacity_change", r"\b(?:capacity|facility|plant|factory|fleet|operational scale|operating footprint|development inventory)\b.{0,160}\b(?:add|expand|open|close|shutdown|increase|reduce|double|order)\w*\b|\b(?:add|expand|open|close|shutdown|increase|reduce|double|order)\w*\b.{0,160}\b(?:capacity|facility|plant|factory|fleet|aircraft|airplanes?|operational scale|operating footprint|development inventory)\b", positive=("add", "expand", "open", "increase", "double", "order"), negative=("close", "shutdown", "reduce")),
    _rule("strategy.strategic_alternatives", r"\b(?:strategic alternatives?|sale process|strategic transaction process|(?:receives?|received)\b.{0,100}\b(?:privatization|capital[- ]alliance|strategic transaction) proposals?)\b", "assessment", positive=("expedited", "advance", "continues", "receives", "received"), negative=("terminat", "abandon", "ends")),
    _rule("governance.auditor_change", r"\b(?:auditor|accounting firm)\b.*\b(?:resign|dismiss|appoint|replace)\w*\b", negative=("resign", "dismiss")),
    _rule("governance.shareholder_vote", r"\b(?:shareholders?|stockholders?)\b.*\b(?:vote|meeting|proposal|proxy|director nominees?)\b|\b(?:proxy|director nominees?)\b.*\b(?:shareholders?|stockholders?)\b"),
    _rule("index.membership", r"\b(?:added to|removed from|join(?:s|ed)?|delete(?:d)?|replac(?:e|es|ed|ing))\b.*\b(?:index|S&P|Russell|Nasdaq-100)\b|\b(?:index|S&P|Russell|Nasdaq-100)\b.*\b(?:added|removed|join(?:s|ed)?|delete(?:d)?|replac(?:e|es|ed|ing))\b", positive=("added", "join"), negative=("removed", "delete")),
    _rule("technology.cybersecurity_incident", r"\b(?:cyberattack|data breach|ransomware|security incident)\b", negative=("attack", "breach", "ransomware", "incident")),
    _rule("market.options_activity", r"\b(?:options activity|call volume|put volume|unusual options)\b", "market_observation"),
    _rule("market.volume_move_observed", r"\b(?:notable |large )?block trades?\b|\bblocks? totaling\b.{0,80}\bshares?\b", "market_observation"),
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
    _rule("analyst.short_thesis", r"\b(?:short|bear)\s+(?:seller\s+)?(?:thesis|report)\b|\b(?:research|short seller)\b.{0,180}\b(?:accus\w*|scam\w*|fraud\w*|challenge\w*)\b", "assessment", negative=("short thesis", "short report", "bear thesis", "accus", "scam", "fraud", "challenge")),
    _rule("analyst.issuer_assessment", r"\b(?:analysts?|brokerage|research firm|investment firm)\b.{0,180}\b(?:believes?|expects?|sees?|views?|said|claims?|argues?|positive|negative|bullish|bearish|upside|downside|recommend(?:s|ed)?|confident|buyer)\b|\b(?:analysts?|[A-Z][a-z]+)\b.{0,40}\b(?:claims?|argues?)\b.{0,180}\b(?:case|claim|lawsuit|action|investigation)\b|\b(?:is not|isn't|was not|wasn't|not)\s+a buyer\b", "assessment", positive=("positive", "bullish", "upside", "strong", "well-positioned", "recommend", "confident"), negative=("negative", "bearish", "downside", "weak")),
    _rule("analyst.issuer_assessment", r"\b(?:out\s+)?in\s+defen[cs]e\s+of\b|\bdefends?\b.{0,100}\b(?:company|issuer|stock|shares?)\b", "assessment", positive=("defense", "defence", "defend"), local_evidence=True),
    _rule("analyst.issuer_assessment", r"\b(?:unlikely|not expected|not likely)\b.{0,40}\b(?:to be |to become )?profitable\b|\bunprofitable\b", "assessment", negative=("unlikely", "not expected", "not likely", "unprofitable"), local_evidence=True),
    _rule("analyst.issuer_assessment", r"\b(?:great|strong|excellent|well)[- ]position(?:ed)?\b", "assessment", positive=("great position", "strong position", "excellent position", "well positioned", "well-positioned"), local_evidence=True),
    _rule("analyst.issuer_assessment", r"\bpublishes?\b.{0,50}\bevidence\b.{0,140}\b(?:hitting|hurting|pressuring|displacing|eroding)\b.{0,80}\b(?:incumbents?|industry|business(?:es)?|demand|sales|rentals?|rental cars?)\b", "assessment", negative=("hitting", "hurting", "pressuring", "displacing", "eroding")),
    _rule("strategy.valuation_assessment", r"\b(?:valuation|valued|multiple|price[- ]to[- ]earnings|P/E|PE|PEG ratio|undervalued|overvalued|cheap|expensive|fully reflect|buyer (?:at|around))\b", "assessment", positive=("undervalued", "cheap", "attractive", "buyer"), negative=("overvalued", "expensive", "premium", "fully reflect")),
    _rule("operations.cost_efficiency", r"\b(?:cost savings?|cost reduction|reduce(?:s|d|ing)? (?:its )?costs?|reduc(?:e|es|ed|ing) operating expenses?|expense reduction|efficiency program|productivity initiative|annual(?:ized)? savings|savings (?:in|on|from) .{0,50}costs?|lower .{0,40}costs?|(?:rising|higher|increased) (?:labor )?costs?|costs? (?:rose|risen|rising|increased|dropped|declined|decreased)|contain costs?|control .{0,30}costs?|total cost of ownership|cost[- ]effectiveness)\b", positive=("savings", "reduction", "reduce", "lower", "efficiency", "productivity", "dropped", "declined"), negative=("higher costs", "rising costs", "rising labor costs", "increased costs", "cost pressure")),
    _rule("macro.policy_outlook", r"\b(?:central bank|Federal Reserve|Fed|government|policy makers?)\b.{0,160}\b(?:policy|stimulus|rate cuts?|rate hikes?|tighten|ease|intervention)\b|\b(?:monetary|fiscal) policy\b", "forecast"),
    _rule("commodity.inventory", r"\b(?:crude oil|oil|natural gas|gasoline) inventor(?:y|ies)\b|\binventor(?:y|ies)\b.{0,80}\b(?:barrels?|crude|oil|gas)\b", "market_observation", positive=("draw", "decline", "fell"), negative=("build", "increase", "rose")),
    _rule("strategy.operational_priority", r"\b(?:strategic priority|operational priority|focus(?:ed|es|ing)?(?: more)? on|plans? to prioritize|key initiative|strategic objective|working on\b.{0,100}\bprojects?|biggest\b.{0,160}\bprogram)\b", "assessment"),
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
        identity_text = text if not title or _normalize_alias(title) in _normalize_alias(text) else f"{title}\n{text}"
        source_field = "rendered_text" if body and body != title else "title"
        if not source_id or not timestamp or not text:
            raise ValueError("source_id, source_timestamp, and source text are required")
        tickers = tuple(dict.fromkeys(
            _normalize_ticker_identifier(value)
            for value in source.get("tickers") or source.get("entity_terms") or ()
            if _normalize_ticker_identifier(value)
        ))
        reviewed_title_policy = classify_reviewed_title_policy(title, tickers=tickers)
        evaluation_tickers = tuple(
            str(value) for value in source.get("evaluation_target_tickers") or () if value
        )
        entities = _dedupe_entities_by_id(
            self.identity_index.resolve(text=identity_text, candidates=tickers, timestamp=timestamp)
        )
        if (
            _has_issuer_scoped_rule(identity_text, self.rules)
            or _has_issuer_event_assertion(identity_text)
            or earnings_call_title_pattern(title) is not None
        ):
            entity_ids = {str(entity["entity_id"]) for entity in entities}
            for candidate in self.identity_index.supported_candidates(
                candidates=tickers,
                timestamp=timestamp,
            ):
                if str(candidate["entity_id"]) not in entity_ids:
                    entities.append(candidate)
                    entity_ids.add(str(candidate["entity_id"]))
            entities.sort(key=lambda row: (str(row.get("ticker") or ""), str(row["entity_id"])))
        entity_ids = {str(entity["entity_id"]) for entity in entities}
        evaluation_only_entity_ids: set[str] = set()
        for candidate in self.identity_index.supported_evaluation_targets(
            candidates=evaluation_tickers,
            timestamp=timestamp,
        ):
            entity_id = str(candidate["entity_id"])
            if entity_id in entity_ids:
                continue
            entities.append(candidate)
            entity_ids.add(entity_id)
            evaluation_only_entity_ids.add(entity_id)
        entities.sort(key=lambda row: (str(row.get("ticker") or ""), str(row["entity_id"])))
        envelope = _envelope(title, text, source)
        document_aliases = _document_ticker_aliases(identity_text)
        mention_terms = {
            str(entity["entity_id"]): tuple(dict.fromkeys((
                *self.identity_index.mention_terms(entity),
                *document_aliases.get(_normalize_ticker_identifier(entity.get("ticker")), ()),
            )))
            for entity in entities
        }
        statements, participations = self._statements(
            text,
            entities,
            source_field,
            mention_terms,
            title=title,
            reference_year=_reference_year(source.get("source_timestamp")),
            allow_single_subject_continuity=(
                envelope["document_structure"]["value"] == "single_subject"
            ),
            evaluation_only_entity_ids=evaluation_only_entity_ids,
        )
        participations = _apply_attributed_claim_sources(
            statements,
            participations,
            entities,
            mention_terms,
            candidate_tickers=tickers,
        )
        participations = _apply_event_supersession(statements, participations)
        statements, participations = _apply_intrinsic_event_tradeoffs(
            statements,
            participations,
            entities,
            mention_terms,
        )
        document_flags = _document_quality_flags(source, text)
        flags = _quality_flags(source, entities, text, document_flags=document_flags)
        if earnings_call_title_pattern(title) is not None:
            flags = sorted({*flags, "earnings_call_transcript"})
        if reviewed_title_policy is not None:
            flags = sorted({*flags, f"reviewed_title_policy:{reviewed_title_policy.family}"})
            if reviewed_title_policy.title_material_flag:
                flags = sorted({*flags, reviewed_title_policy.title_material_flag})
        views = derive_issuer_views(entities, participations, statements=statements)
        synthesis = derive_synthesis(entities=entities, statements=statements, participations=participations, issuer_views=views)
        eligibility = derive_eligibility(
            entities=entities,
            statements=statements,
            participations=participations,
            envelope=envelope,
            quality_flags=flags,
            document_quality_flags=document_flags,
        )
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
        *,
        title: str = "",
        reference_year: int | None = None,
        allow_single_subject_continuity: bool = True,
        evaluation_only_entity_ids: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        statements: list[dict[str, Any]] = []
        participations: list[dict[str, Any]] = []
        previous_entity_ids: tuple[str, ...] = ()
        previous_guidance_entity_ids: tuple[str, ...] = ()
        previous_guidance = False
        previous_performance = False
        previous_end = 0
        local_fallback_seen: set[tuple[str, str, int, int]] = set()
        guidance_rule = next(rule for rule in self.rules if rule.concept == "guidance.issued")
        earnings_rule = next(rule for rule in self.rules if rule.concept == "earnings.performance")
        clinical_regulatory_rule = next(rule for rule in self.rules if rule.concept == "clinical.regulatory_milestone")
        rules_by_concept = {rule.concept: rule for rule in self.rules}
        lanes: list[tuple[str, str]] = [(source_field, text)]
        # Preserve a separately supplied headline, but do not duplicate the
        # canonical renderer's leading ``Title: ...`` line.
        if (
            title
            and title.strip() != text.strip()
            and _normalize_alias(title) not in _normalize_alias(text)
        ):
            lanes.append(("title", title))
        semantic_rows = [
            (lane_index, lane_field, lane_text, start, end, quote)
            for lane_index, (lane_field, lane_text) in enumerate(lanes)
            for start, end, quote in _semantic_spans(lane_text)
        ]
        previous_lane = -1
        navigation_block = False
        for semantic_index, (
            lane_index, lane_field, lane_text, start, end, quote
        ) in enumerate(semantic_rows):
            if lane_index != previous_lane:
                previous_entity_ids = ()
                previous_guidance_entity_ids = ()
                previous_guidance = False
                previous_performance = False
                previous_end = 0
                navigation_block = False
            if len(quote) < 8:
                previous_lane = lane_index
                continue
            if _boilerplate_sentence(quote) or _forward_looking_boilerplate_sentence(quote):
                navigation_block = navigation_block or bool(re.match(
                    r"\s*related links?\s*:?\s*$",
                    quote,
                    re.I,
                ))
                previous_end = end
                previous_lane = lane_index
                continue
            if "\n\n" in lane_text[previous_end:start]:
                previous_entity_ids = ()
                previous_guidance_entity_ids = ()
                previous_guidance = False
                previous_performance = False
                navigation_block = False
            if navigation_block:
                previous_end = end
                previous_lane = lane_index
                continue
            matched_rules = [
                (rule, match)
                for rule in self.rules
                if (match := rule.pattern.search(quote))
                and _rule_applicable(
                    rule,
                    quote,
                    context=lane_text[max(0, start - 80):min(len(lane_text), end + 120)],
                )
            ]
            regulatory_facts = extract_regulatory_decision_facts(quote)
            if (
                regulatory_facts
                and not any(rule.concept == "clinical.regulatory_milestone" for rule, _match in matched_rules)
            ):
                matched_rules.append((clinical_regulatory_rule, None))
            if previous_guidance and _coordinated_guidance_fragment(quote):
                matched_rules = [
                    (rule, match)
                    for rule, match in matched_rules
                    if rule.concept not in {"earnings.performance", "financial.operating_performance"}
                ]
                if not any(rule.concept == "guidance.issued" for rule, _match in matched_rules):
                    matched_rules.append((guidance_rule, None))
            if (
                previous_performance
                and _coordinated_result_fragment(quote)
                and not any(rule.concept == "earnings.performance" for rule, _match in matched_rules)
            ):
                matched_rules.append((earnings_rule, None))
            inherit_subject = any(_issuer_scoped_concept(rule.concept) for rule, _match in matched_rules)
            scoped_entities = _entities_for_quote(
                entities,
                quote,
                previous_entity_ids,
                mention_terms,
                inherit_subject=inherit_subject,
                require_grounded_entity_ids=evaluation_only_entity_ids,
            )
            if lane_field == "title" and (
                regulatory_facts
                or any(
                    rule.concept == "clinical.regulatory_milestone"
                    for rule, _match in matched_rules
                )
            ):
                scoped_entities = _relationship_scoped_regulatory_entities(
                    entities,
                    scoped_entities,
                    quote,
                    mention_terms,
                    regulatory_facts,
                )
            if scoped_entities:
                previous_entity_ids = tuple(str(row["entity_id"]) for row in scoped_entities)
            if lane_field == "title" and scoped_entities and matched_rules:
                # Body evidence carries the fuller event package.  Do not let
                # an equivalent compressed headline discard body-only
                # qualifiers such as ownership or proceeds. Distinct events
                # sharing only an entity and concept remain independent.
                matched_rules = [
                    (rule, match)
                    for rule, match in matched_rules
                    if any(
                        not _equivalent_prior_event(
                            rule,
                            quote,
                            entity,
                            statements,
                            participations,
                            reference_year=reference_year,
                        )
                        for entity in scoped_entities
                    )
                ]
            span_participation_start = len(participations)
            span_guidance_entity_ids: list[str] = []
            for rule, match in matched_rules:
                sid = f"s{len(statements) + 1:04d}"
                statement_quote = match.group(0) if rule.local_evidence and match is not None else quote
                statement_start = start + match.start() if rule.local_evidence and match is not None else start
                statement_end = start + match.end() if rule.local_evidence and match is not None else end
                span = {
                    "source_field": lane_field,
                    "start": statement_start,
                    "end": statement_end,
                    "quote": statement_quote,
                }
                estimate_role = (
                    "issuer_guidance"
                    if rule.concept == "guidance.issued"
                    else "analyst_estimate"
                    if rule.concept == "estimate.revision"
                    else "reported_result"
                    if rule.concept in {"earnings.performance", "financial.operating_performance"}
                    else "issuer_guidance"
                )
                fact_span = span
                evidence_spans = [span]
                if rule.concept == "guidance.issued":
                    fact_quote, comparator_span = _guidance_fact_context(
                        semantic_rows,
                        semantic_index,
                        quote,
                        lane_text,
                        end,
                    )
                    if fact_quote != statement_quote:
                        fact_span = {
                            **span,
                            "end": comparator_span["end"],
                            "quote": fact_quote,
                        }
                        evidence_spans.append(comparator_span)
                typed_facts = extract_typed_facts(
                    [fact_span],
                    estimate_subject_role=estimate_role,
                )
                relation_text = quote
                statements.append({"statement_id": sid, "statement_kind": rule.statement_kind, "concept_leaf": rule.concept, "epistemic_status": _epistemic(quote), "time_relation": _time_relation(relation_text, rule.statement_kind, reference_year=reference_year), "evidence_spans": evidence_spans, "typed_facts": typed_facts})
                rule_entities = scoped_entities
                if rule.concept in {"clinical.regulatory_milestone", "regulatory.action"}:
                    rule_entities = _regulatory_entities_for_facts(
                        scoped_entities,
                        statement_quote,
                        typed_facts,
                        mention_terms,
                    )
                elif rule.concept == "clinical.trial_result" and re.search(
                    r"\bbiosimilar\b.{0,80}\bhighly similar\b.{0,40}\bto\b",
                    statement_quote,
                    re.I,
                ):
                    rule_entities = [
                        entity
                        for entity in entities
                        if _entity_in_quote(
                            entity,
                            statement_quote,
                            mention_terms.get(str(entity["entity_id"]), ()),
                        )
                        or _entity_possessive_in_quote(
                            entity,
                            statement_quote,
                            mention_terms.get(str(entity["entity_id"]), ()),
                        )
                    ]
                elif rule.concept == "guidance.issued":
                    rule_entities = _guidance_entities_for_quote(
                        scoped_entities,
                        statement_quote,
                        mention_terms,
                        inherited_entity_ids=previous_guidance_entity_ids,
                    )
                    span_guidance_entity_ids.extend(
                        str(row["entity_id"]) for row in rule_entities
                    )
                for entity in rule_entities:
                    role = _semantic_role(quote, entity, rule.concept)
                    sentiment_text = statement_quote
                    if rule.concept == "capital.financing":
                        sentiment_text = _financing_sentiment_text(
                            lane_text,
                            start,
                            end,
                            statement_quote,
                            entity,
                            mention_terms.get(str(entity["entity_id"]), ()),
                        )
                    sentiment, strength = _sentiment(
                        sentiment_text,
                        rule,
                        role,
                        typed_facts,
                        entity=entity,
                        mention_terms=mention_terms.get(str(entity["entity_id"]), ()),
                    )
                    participations.append({"statement_id": sid, "entity_id": entity["entity_id"], "semantic_role": role, "discourse_role": "none", "semantic_sentiment": sentiment, "sentiment_strength": strength})
            canonical_entity_ids = {
                str(row.get("entity_id") or "")
                for row in participations[span_participation_start:]
            }
            fallback_entities = [
                entity
                for entity in entities
                if str(entity.get("identity_status") or "resolved") == "resolved"
                and str(entity.get("entity_id") or "") not in canonical_entity_ids
                and _entity_in_quote(
                    entity,
                    quote,
                    mention_terms.get(str(entity.get("entity_id") or ""), ()),
                )
                and _entity_is_local_event_subject(
                    entity,
                    quote,
                    mention_terms.get(str(entity.get("entity_id") or ""), ()),
                )
            ]
            fallback_polarity = _strict_local_fallback_sentiment(quote)
            if fallback_entities and fallback_polarity is not None:
                fallback_rule = rules_by_concept["operations.business_update"]
                sid = f"s{len(statements) + 1:04d}"
                span = {
                    "source_field": lane_field,
                    "start": start,
                    "end": end,
                    "quote": quote,
                }
                fallback_statement = {
                    "statement_id": sid,
                    "statement_kind": fallback_rule.statement_kind,
                    "concept_leaf": fallback_rule.concept,
                    "epistemic_status": _epistemic(quote),
                    "time_relation": _time_relation(
                        quote,
                        fallback_rule.statement_kind,
                        reference_year=reference_year,
                    ),
                    "evidence_spans": [span],
                    "typed_facts": extract_typed_facts([span]),
                }
                emitted = False
                fallback_sentiment, fallback_strength = fallback_polarity
                for entity in sorted(fallback_entities, key=lambda row: str(row["entity_id"])):
                    key = (str(entity["entity_id"]), lane_field, start, end)
                    if key in local_fallback_seen:
                        continue
                    local_fallback_seen.add(key)
                    participations.append({
                        "statement_id": sid,
                        "entity_id": entity["entity_id"],
                        "semantic_role": "affected_subject",
                        "discourse_role": "none",
                        "semantic_sentiment": fallback_sentiment,
                        "sentiment_strength": fallback_strength,
                    })
                    emitted = True
                if emitted:
                    statements.append(fallback_statement)
            previous_guidance = any(rule.concept == "guidance.issued" for rule, _match in matched_rules)
            if span_guidance_entity_ids:
                previous_guidance_entity_ids = tuple(dict.fromkeys(span_guidance_entity_ids))
            previous_performance = any(rule.concept == "earnings.performance" for rule, _match in matched_rules)
            previous_end = end
            previous_lane = lane_index

        issuer_statements = [
            statement
            for statement in statements
            if _issuer_scoped_concept(str(statement.get("concept_leaf") or ""))
        ]
        participating_ids = {
            str(participation.get("entity_id") or "")
            for participation in participations
        }
        participating_pairs = {
            (
                str(participation.get("entity_id") or ""),
                str(participation.get("statement_id") or ""),
            )
            for participation in participations
        }
        for entity in entities:
            entity_id = str(entity.get("entity_id") or "")
            if not entity_id:
                continue
            require_local_scope = (
                entity_id in evaluation_only_entity_ids
                or (len(entities) > 1 and entity_id in participating_ids)
            )
            scoped_statements = (
                [
                    statement for statement in issuer_statements
                    if (entity_id, str(statement.get("statement_id") or ""))
                    not in participating_pairs
                ]
                if (
                    (len(entities) == 1 and not require_local_scope)
                    or (allow_single_subject_continuity and not require_local_scope)
                )
                else [
                    statement
                    for statement in issuer_statements
                    if _entity_in_quote(
                        entity,
                        str((statement.get("evidence_spans") or [{}])[0].get("quote") or ""),
                        mention_terms.get(entity_id, ()),
                    )
                ]
            )
            for statement in scoped_statements:
                rule = rules_by_concept.get(str(statement.get("concept_leaf") or ""))
                if rule is None:
                    continue
                pair = (entity_id, str(statement.get("statement_id") or ""))
                if pair in participating_pairs:
                    continue
                quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
                if rule.concept == "guidance.issued":
                    guidance_entity_ids = {
                        str(row.get("entity_id") or "")
                        for row in _guidance_entities_for_quote(
                            entities,
                            quote,
                            mention_terms,
                            allow_unmentioned=False,
                        )
                    }
                    if entity_id not in guidance_entity_ids:
                        continue
                if rule.concept in {"clinical.regulatory_milestone", "regulatory.action"}:
                    explicit_regulatory_ids = {
                        str(row.get("entity_id") or "")
                        for row in entities
                        if _entity_in_quote(
                            row,
                            quote,
                            mention_terms.get(str(row.get("entity_id") or ""), ()),
                        )
                    }
                    if explicit_regulatory_ids and entity_id not in explicit_regulatory_ids:
                        continue
                if (
                    len(entities) > 1
                    and statement.get("concept_leaf") == "operations.business_update"
                    and not _entity_is_local_event_subject(
                        entity,
                        quote,
                        mention_terms.get(entity_id, ()),
                    )
                ):
                    continue
                role = _semantic_role(quote, entity, rule.concept)
                sentiment, strength = _sentiment(
                    quote,
                    rule,
                    role,
                    statement.get("typed_facts", ()),
                    entity=entity,
                    mention_terms=mention_terms.get(entity_id, ()),
                )
                participations.append({
                    "statement_id": statement["statement_id"],
                    "entity_id": entity_id,
                    "semantic_role": role,
                    "discourse_role": "none",
                    "semantic_sentiment": sentiment,
                    "sentiment_strength": strength,
                })
                participating_pairs.add(pair)
        return statements, participations


def _envelope(
    title: str,
    text: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    combined = f"{title}\n{text}"
    provider_context = classify_provider_context(source)
    metadata = " ".join(str(x) for name in ("channels", "provider_tags") for x in source.get(name) or ())
    author = str(source.get("author") or "").strip().casefold()
    title_policy = classify_reviewed_title_policy(
        title,
        tickers=tuple(str(value) for value in source.get("tickers") or ()),
    )
    article_url = str(source.get("article_url") or source.get("url_domain") or "").casefold()
    list_title = bool(re.search(
        r"\b(?:calendar|watch list|stocks to watch|top \d+|\d+ stocks|analyst color|price target changes|"
        r"earnings scheduled (?:for|on)|companies reporting (?:before|after)|\d+ things to know)\b",
        title,
        re.I,
    ))
    market_title = re.search(
        r"\b(?:market|morning)\s+(?:wrap|overview|recap|update|capsule)\b|\bbig picture\b|"
        r"\b(?:stock|index|treasury|commodity|currency|forex|cattle|sugar|natural gas|crude oil) futures?\b|"
        r"\b(?:dollar index|forex:|wall street|what'?s moving markets|S&P 500 (?:turns|futures?|extends?|falls?|rises?))\b|"
        r"\b(?:jobless claims|jobs report|gross domestic product|GDP|inflation|deflation|consumer prices?|CPI|"
        r"credit card debt|pending home sales|wholesale inventories|crude oil inventories)\b|"
        r"\b(?:stock market today|market primer|markets (?:gather|gain|lose|rally|fall|rise|close|open))\b|"
        r"\bBitcoin\b.{0,120}\bEthereum\b",
        title,
        re.I,
    )
    market_body_structure = re.search(
        r"\b(?:morning capsule|midday market update|mid-afternoon market update|market close recap|"
        r"equities trading (?:up|down)|leading and lagging sectors|news of note|major indices)\b|"
        r"\bU\.S\. stocks (?:traded|were trading|opened|closed)\b",
        combined[:4000],
        re.I,
    )
    market_overview = bool(market_title or market_body_structure)
    digest = bool(
        ROUNDUP_RE.search(title)
        or re.search(
            r"\b(?:movers|gainers|losers|market roundup|analyst ratings|stocks? to watch|"
            r"top (?:upgrades|downgrades)|catalysts? to watch|tweets? for|headlines? roundup|"
            r"roundup of (?:the )?(?:top|key|latest)?\s*(?:news|headlines|developments)|"
            r"market-moving news|daily [a-z-]+ pulse|52-week (?:highs?|lows?)|"
            r"(?:auto sales|M&A|merger|deal|earnings) (?:summary|roundup|chatter)|"
            r"weekend M&A chatter|market primer)\b",
            combined[:1200],
            re.I,
        )
    )
    if list_title: structure = "reference_list"
    elif market_overview: structure = "market_overview"
    elif digest: structure = "multi_subject_digest"
    else: structure = "single_subject"
    reviewed_mover_pattern = why_moving_title_pattern(title)
    earnings_call_pattern = earnings_call_title_pattern(title)
    causal_mover = bool(
        reviewed_mover_pattern
        or WHY_MOVING_RE.search(title)
        or re.search(r"\bwhy is\b.{0,100}\b(?:up|down)\b(?:.{0,80}\bsince\b)?", title, re.I)
        or re.search(r"\b(?:why|what(?:'s| is) (?:up|going on) with)\b.{0,100}\b(?:shares?|stock)\b", title, re.I)
        or re.search(r"\b(?:shares?|stock)\b.{0,80}\b(?:up|down|higher|lower|rall(?:y|ies|ied|ying)|surg(?:e|es|ed|ing)|soar(?:s|ed|ing)?|spik(?:e|es|ed|ing)|jump(?:s|ed|ing)?|fall(?:s|ing)?|fell|rose|gain(?:s|ed)?|drop(?:s|ped|ping)?|slid(?:e|ing)?|sink(?:s|ing)?|sank|crash(?:es|ed|ing)?|tumble[sd]?|hammered|unaffected)\b.{0,80}\b(?:after|following|on|as|amid|because|here'?s why)\b", title, re.I)
        or re.search(r"\b(?:shares?|stock)\s+(?:are\s+)?trading\s+(?:up|down|higher|lower)\b.{0,80}\b(?:after|following|on|as|amid)\b", title, re.I)
        or (
            re.search(
                r"\b(?:up|down)\s+\d+(?:\.\d+)?%\s*\([A-Z][A-Z0-9.]{0,9}\)\s*$",
                title,
                re.I,
            )
            and not re.search(
                r"\b(?:sales?|revenue|earnings?|profit|income|orders?|shipments?|deliveries|production|volume|margin|EPS)\b.{0,50}\b(?:up|down)\s+\d+(?:\.\d+)?%",
                title,
                re.I,
            )
        )
    )
    reviewed_mover_override = (
        reviewed_mover_pattern == "why_or_what_price_move_question"
        or bool(reviewed_mover_pattern and not market_overview)
    )
    if earnings_call_pattern: purpose = "report"
    elif title_policy is not None: purpose = title_policy.purpose
    elif reviewed_mover_override or (causal_mover and not market_overview): purpose = "explain_move"
    elif list_title or re.search(
        r"\b(?:ahead of|preview|what to expect|will report|to watch)\b|"
        r"\bscheduled to (?:report|announce|release|publish)\b|"
        r"\b(?:gearing up to report|before market opens|future earnings|IPO (?:preview|seeks|plans?))\b",
        title,
        re.I,
    ): purpose = "preview"
    elif structure in {"market_overview", "multi_subject_digest"}: purpose = "recap"
    elif re.search(
        r"\b(?:technical analysis|what investors should know|what you need to know|"
        r"case for|bull case|bear case|valuation|outlook for|analyst (?:view|take|note|blog)|"
        r"research report|our take|stock on the radar|earnings trends|full transcript|"
        r"insights into .{0,60} earnings|ETF news and commentary|"
        r"bull and bear of the day|bear of the day|traders? share .{0,60} thoughts|"
        r"gives? color|power inflow alert|stock picks?|how safe is|can .{0,100}\?|opinion)\b|"
        r"\banalysis\b",
        title,
        re.I,
    ): purpose = "analyze"
    elif re.search(
        r"\b(?:read the full .{0,80} research report|initiating coverage|analyst blog|"
        r"Zacks Rank|our viewpoint|technical charts?|entry points?|investment thesis)\b",
        combined[:4000],
        re.I,
    ): purpose = "analyze"
    else: purpose = "report"
    analyst_match = _first_match(
        (
            r"\b(?:analysts?(?!\s+(?:est\.?|estimates?|consensus|expectations)\b)|research firm|"
            r"price target|(?:buy|sell|hold|overweight|underweight|equal-weight) rating)\b|"
            r"\b(?:upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?)\b.{0,80}"
            r"\b(?:rating|coverage|price target|\bPT\b|\bPO\b|buy|sell|hold|overweight|underweight|equal-weight)\b",
            ATTRIBUTED_ASSESSMENT_RE.pattern,
        ),
        title,
        text,
    )
    if analyst_match is None and (
        re.search(r"\b(?:Zacks (?:Investment|Equity) Research|Zacks Rank|Analyst Blog|Bear of the Day)\b", combined[:4000], re.I)
        or re.search(r"\b(?:CFA|equity research|research analyst)\b.{0,160}\b(?:research report|coverage|price target|valuation)\b", combined[:4000], re.I)
        or re.search(r"\b(?:coverage|price target|\bPT\b|\bPO\b|outperform|underperform|neutral)\b.{0,80}\b(?:upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?|raises?|cuts?)\b", combined[:4000], re.I)
    ):
        analyst_match = _first_match((
            r"\b(?:Zacks (?:Investment|Equity) Research|Zacks Rank|Analyst Blog|Bear of the Day)\b|"
            r"\b(?:CFA|equity research|research analyst)\b.{0,160}\b(?:research report|coverage|price target|valuation)\b|"
            r"\b(?:coverage|price target|\bPT\b|\bPO\b|outperform|underperform|neutral)\b.{0,80}"
            r"\b(?:upgrade[sd]?|downgrade[sd]?|initiates?|maintains?|reiterates?|raises?|cuts?)\b",
        ), title, text)
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
    issuer_transcript_match = _first_match((
        r"(?:\b(?:earnings|conference)\s+(?:conference\s+)?call\b.{0,160}\btranscript\b|"
        r"\btranscript\b.{0,160}\b(?:earnings|conference)\s+(?:conference\s+)?call\b)",
        r"\bduring\s+(?:the\s+)?(?:conf(?:erence)?\.?|earnings)\s+call\b",
    ), title, "")
    analyst_origin = analyst_match is not None
    regulator_origin = regulator_match is not None
    issuer_match = issuer_body_match or issuer_transcript_match or (
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
    if title_policy is not None:
        origin = title_policy.origin
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
        "provider_context": provider_context,
        "document_structure": decision(structure, "envelope.structure.v1"),
        "communication_purpose": decision(
            purpose,
            (
                f"envelope.purpose.earnings_call_v1:{earnings_call_pattern}"
                if earnings_call_pattern
                else f"envelope.purpose.reviewed_title_policy_v1:{title_policy.family}"
                if title_policy is not None
                else f"envelope.purpose.why_moving_title_v2:{reviewed_mover_pattern}"
                if reviewed_mover_pattern
                else "envelope.purpose.v1"
            ),
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


def _document_quality_flags(source: Mapping[str, Any], text: str) -> list[str]:
    flags = {str(value) for name in ("quality_flags", "content_quality_flags") for value in source.get(name) or () if value}
    if not text.strip(): flags.add("invalid_text")
    if str(source.get("render_status") or "").strip().lower() == "unrendered": flags.add("unrendered_text")
    return sorted(flags)


def _quality_flags(
    source: Mapping[str, Any],
    entities: Sequence[Mapping[str, Any]],
    text: str,
    *,
    document_flags: Iterable[str] | None = None,
) -> list[str]:
    flags = set(
        _document_quality_flags(source, text)
        if document_flags is None
        else document_flags
    )
    if not entities: flags.add("unresolved_identity")
    for row in entities:
        if row["identity_status"] in {"ambiguous", "unresolved"}: flags.add(f"{row['identity_status']}_identity")
    return sorted(flags)


def _epistemic(text: str) -> str:
    return "rumored" if re.search(r"\b(?:rumor|reportedly|may be|could be)\b", text, re.I) else "conditional" if re.search(r"\b(?:if|subject to)\b", text, re.I) else "planned" if re.search(r"\b(?:plans?|intends?|will)\b", text, re.I) else "expected" if re.search(r"\b(?:expects?|forecast|guidance|project(?:s|ed|ing|ion)s?|evaluat(?:e|es|ed|ing)|consider(?:s|ed|ing)?|explor(?:e|es|ed|ing)|attempt(?:s|ed|ing)?)\b", text, re.I) else "confirmed"


def _contains_explicit_result_comparison(text: str) -> bool:
    if re.search(
        r"\b(?:guidance|outlook|forecast|sees|expects?|projects?|anticipates?|reaffirms?)\b",
        text,
        re.I,
    ):
        return False
    return bool(re.search(
        r"\b(?:adjusted\s+|diluted\s+)?(?:EPS|earnings per share|revenues?|sales)\b"
        r".{0,120}\b(?:vs\.?|versus|compared (?:with|to))\b"
        r".{0,80}\b(?:est\.?|estimates?|consensus)\b",
        text,
        re.I,
    ))


def _rule_applicable(rule: ConceptRule, text: str, *, context: str | None = None) -> bool:
    """Apply cross-cutting semantic gates that cannot be expressed safely as noun regexes."""
    if rule.concept == "guidance.issued":
        if (
            re.search(r"\brais(?:e|es|ed|ing)\b.{0,80}\b(?:guidance|outlook|forecast)\b", text, re.I)
            and not re.search(r"\b(?:cuts?|lowers?|reduces?|withdraws?)\b", text, re.I)
            and re.search(
                r"\b(?:prior|earlier|previous)\b.{0,100}\b(?:headline|report|statement)\b"
                r".{0,120}\brais(?:e|es|ed|ing)\b.{0,80}\b(?:guidance|outlook|forecast)\b"
                r".{0,120}\b(?:incorrect|inaccurate|wrong|error)\b",
                context or text,
                re.I,
            )
        ):
            # A correction negates the proposition in the preceding clause;
            # it is not a second current issuer-guidance event.
            return False
        explicit_guidance_semantics = re.search(
            r"\b(?:guidance|outlook|forecast|projection)\b|"
            r"\b(?:sees|expects?|anticipates?|projects?|targets?|is looking for)\b"
            r".{0,120}\b(?:rev\.?|revenue|sales|earnings|EPS|EBITDA|growth|margin|free cash flow|"
            r"comparable[- ]store sales|same[- ]store sales|comps?|comp)\b|"
            r"\bexpected to\s+(?:comp|report comparable[- ]store sales|report same[- ]store sales)\b|"
            r"\b(?:raises?|boost(?:s|ed)?|lowers?|cuts?|reaffirm(?:s|ed)?|withdraws?)\b"
            r".{0,60}\b(?:FY\d{0,4}|Q[1-4]|full[- ]year|fiscal[- ]year)\b"
            r".{0,60}\b(?:rev\.?|revenue|sales|earnings|EPS|EBITDA|growth|margin|free cash flow)\b",
            text,
            re.I,
        )
        bare_increase_or_boost = re.search(
            r"\b(?:boost(?:s|ed|ing)?|increas(?:e[sd]?|ing)|rais(?:e[sd]?|ing)|"
            r"lower(?:s|ed|ing)?|decreas(?:e[sd]?|ing)|reduc(?:e[sd]?|ing)|cuts?)\b",
            text,
            re.I,
        )
        if bare_increase_or_boost and explicit_guidance_semantics is None:
            return False
        external_expectation = re.search(r"\b(?:analysts? expect|analysts? estimate|bulls? (?:will )?hope|predictions? for|consensus (?:calls|expects))\b", text, re.I)
        external_estimate_action = re.search(
            r"\b(?:analysts?|research (?:firm|analyst)|brokerage|the firm|we)\b"
            r".{0,80}\b(?:rais(?:e[sd]?|ing)|increas(?:e[sd]?|ing)|lower(?:s|ed|ing)?|"
            r"reduc(?:e[sd]?|ing)|cuts?|revis(?:e[sd]?|ing))\b"
            r".{0,80}\b(?:EPS|earnings|revenue|sales|price target|estimates?|forecast)\b",
            text,
            re.I,
        )
        explicit_issuer_action = re.search(
            r"\b(?:issue|issued|issuing|provided|guid(?:e[sd]?|ing)|rais(?:e[sd]?|ing)|"
            r"lower(?:s|ed|ing)?|cuts?|reaffirm(?:s|ed|ing)?|withdraw(?:s|n|ing)?|updated)\b",
            text,
            re.I,
        )
        if external_estimate_action:
            return False
        if external_expectation and not explicit_issuer_action:
            return False
    if rule.concept == "earnings.performance" and re.search(
        r"\b(?:estimate|forecast)s?\b|\b(?:note to clients|research note|out with (?:its|a) report)\b",
        text,
        re.I,
    ):
        realized_metric = re.search(
            r"\b(?:reports?|reported|posted)\b.{0,60}\b(?:EPS|earnings|revenue|sales|net income|profit)\b"
            r"\s*(?:of|at|=)?\s*(?:E?\$|Ã‚Â£|Ã¢â€šÂ¬|\(?-?\d)|"
            r"\b(?:beats?|miss(?:ed|es)?|actual|quarterly results?|financial results?)\b",
            text,
            re.I,
        )
        if realized_metric is None:
            realized_metric = re.search(
                r"\b(?:EPS|earnings|revenues?|sales)\b\s+(?:was|were)\s+"
                r".{0,50}\b(?:vs\.?|versus)\s+"
                r"(?:analysts?'?\s+)?(?:estimates?|consensus)\b",
                text,
                re.I,
            )
        if realized_metric is None:
            return False
    if rule.concept in {"earnings.performance", "financial.operating_performance", "financial.cash_flow", "financial.liquidity"}:
        explicit_metric_guidance = re.search(
            r"\b(?:EPS|earnings|revenue|sales|EBITDA|margin)\s+"
            r"(?:guidance|outlook|forecast)\b|"
            r"\b(?:guidance|outlook|forecast)\b.{0,80}"
            r"\b(?:EPS|earnings|revenue|sales|EBITDA|margin)\b",
            text,
            re.I,
        )
        realized_reporting = re.search(
            r"\b(?:reports?|reported|posts?|posted|actual results?)\b",
            text,
            re.I,
        )
        if explicit_metric_guidance and not realized_reporting:
            return False
        projected = re.search(r"\b(?:forecast|guidance|guid(?:e[sd]?|ing)|project(?:s|ed|ing|ion)s?|estimate[sd]?|anticipates?|expects?|sees|reaffirm(?:s|ed|ing)?|is looking for|potential|could|may)\b", text, re.I)
        observed = re.search(r"\b(?:reports?|reported|actual|trailing[- ]twelve[- ]month|TTM|beats?|miss(?:ed|es)?|better[- ]than[- ]expected|weaker[- ]than[- ]expected|rose|fell|grew|declined|slipped|climbed|increased|decreased|recovered|record)\b", text, re.I)
        if projected and not observed and not _contains_explicit_result_comparison(text):
            return False
    if rule.concept == "financial.margin":
        projected = re.search(r"\b(?:forecast|guidance|project(?:s|ed|ing|ion)s?|estimate[sd]?|anticipates?|expects?|cautious|could|may)\b", text, re.I)
        explicit_condition = re.search(
            r"\b(?:margin pressure|margins? (?:under pressure|pressured|compress\w*|expand\w*|improv\w*|declin\w*)|"
            r"pressure on margins?|input cost (?:pressure|inflation)|(?:rising|higher|escalating) input costs?)\b",
            text,
            re.I,
        )
        if projected and explicit_condition is None:
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
    if rule.concept == "capital.financing" and re.search(
        r"\b(?:factors? (?:are |were )?(?:discussed|described|identified)|contained|disclosed)\b"
        r".{0,160}\b(?:registration statement|initial public offering|IPO)\b|"
        r"\b(?:registration statement|initial public offering|IPO)\b"
        r".{0,160}\b(?:risk factors?|forward[- ]looking|previously filed)\b|"
        r"\binformation (?:about|regarding)\b.{0,160}\b(?:set forth|contained)\b"
        r".{0,120}\b(?:prospectus|initial public offering|IPO)\b",
        text,
        re.I,
    ):
        # A filing cited as risk-factor provenance is not a newly announced
        # financing transaction.
        return False
    if rule.concept == "product.milestone" and re.search(
        r"\bno\b.{0,80}\b(?:clinical )?(?:data|evidence|information)\b.{0,80}\bavailable\b|"
        r"\b(?:clinical )?(?:data|evidence|information)\b.{0,80}\bnot available\b",
        text,
        re.I,
    ):
        # Availability of evidence is not commercial product availability,
        # especially when the evidence is explicitly absent.
        return False
    if rule.concept == "financial.loss_exposure" and re.search(
        r"\b(?:renal|kidney|vision|hearing|cognitive|physical) impairment\b|"
        r"\bpatients? with\b.{0,80}\bimpairment\b",
        text,
        re.I,
    ) and not re.search(
        r"\b(?:asset|goodwill|intangible|inventory|financial|accounting) impairment\b|"
        r"\bimpairment charge\b",
        text,
        re.I,
    ):
        return False
    if rule.concept == "financial.loss_exposure" and re.search(
        r"\b(?:in|takes?|taking|assumes?|assuming) charge of\b",
        text,
        re.I,
    ) and not re.search(
        r"\b(?:impairment|restructuring|accounting|financial|one[- ]time|non[- ]cash) charge\b",
        text,
        re.I,
    ):
        # Responsibility idiom, not an expense or loss recognition event.
        return False
    if rule.concept == "financial.loss_exposure" and re.fullmatch(
        r"\s*(?:take|taking|takes?) charge[.!]?\s*",
        text,
        re.I,
    ):
        return False
    if rule.concept == "financial.loss_exposure" and re.search(
        r"\b(?:available|obtain(?:s|ed|ing)?|provided?|download(?:ed|able)?)\b.{0,80}\bfree of charge\b|"
        r"\b(?:documents?|copies|filings?|proxy statements?)\b.{0,80}\b(?:free of|without) charge\b|"
        r"\b(?:free of|without) charge\b.{0,120}\b(?:sec|website|documents?|copies|filings?|proxy statements?)\b",
        text,
        re.I,
    ):
        return False
    if rule.concept == "operations.business_update" and re.search(
        r"\b(?:loan|credit|borrower)\b.{0,100}\brestructur\w*\b|"
        r"\brestructur\w*\b.{0,100}\b(?:loan|credit|borrower)\b",
        text,
        re.I,
    ) and re.search(
        r"\b(?:credit enhancements?|additional collateral|not expected\b.{0,80}\b(?:loss|charge)|"
        r"no (?:expected )?(?:loss|charge))\b",
        text,
        re.I,
    ):
        return False
    if rule.concept == "operations.business_update" and re.search(
        r"\brestructuring (?:costs?|charges?|expenses?)\b.{0,50}"
        r"\b(?:decreas(?:e[sd]?|ing)|declin(?:e[sd]?|ing)|fell|lower)\b",
        text,
        re.I,
    ):
        return False
    if rule.concept == "guidance.issued" and re.search(
        r"\b(?:Fed|Federal Reserve|central bank|policy)\b.{0,80}\brate cuts?\b|"
        r"\brate cuts?\b.{0,80}\b(?:Fed|Federal Reserve|central bank|policy)\b",
        text,
        re.I,
    ) and not re.search(
        r"(?<!rate )\b(?:cuts?|lowers?|reduces?)\b.{0,20}\b(?:its |the |full[- ]year )?"
        r"(?:guidance|outlook|forecast)\b",
        text,
        re.I,
    ):
        return False
    if rule.concept == "strategy.valuation_assessment" and re.search(
        r"\bexpensive\s+(?:capital|debt|funding|financing)\b",
        text,
        re.I,
    ) and not re.search(
        r"\b(?:valuation|valued|stock|shares?|equity|price|multiple|P/E|PE|PEG|overvalued)\b",
        text,
        re.I,
    ):
        return False
    if rule.concept == "analyst.rating_action" and re.search(
        r"\b(?:loan|credit|borrower|internal risk)\b.{0,100}\b(?:rating|upgrade|downgrade)\w*\b|"
        r"\b(?:rating|upgrade|downgrade)\w*\b.{0,100}\b(?:loan|credit|borrower|internal risk)\b",
        text,
        re.I,
    ) and not re.search(
        r"\b(?:analysts?|brokerage|research firm|price target|buy|sell|outperform|underperform|"
        r"overweight|underweight|market perform|sector perform)\b",
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
    if rule.concept == "analyst.issuer_assessment" and re.search(
        r"\b(?:unlikely|not expected|not likely)\b.{0,40}\b(?:to be |to become )?profitable\b|"
        r"\bunprofitable\b|\b(?:great|strong|excellent|well)[- ]position(?:ed)?\b",
        text,
        re.I,
    ) and not (ANALYST_RE.search(text) or ATTRIBUTED_ASSESSMENT_RE.search(text)):
        # These phrases are assessments only when an external source is
        # actually attributed. Issuer self-description must not be relabeled
        # as analyst commentary merely because it uses the same adjective.
        return False
    if rule.concept == "market.price_move_observed" and re.search(r"\b(?:dollar index|currency|forex|euro|yen|yuan|pound sterling)\b", text, re.I):
        return False
    if rule.concept == "market.price_move_observed" and re.search(
        r"\b(?:earnings|revenue|sales)\s+ESP\b",
        text,
        re.I,
    ):
        return False
    return True


def _time_relation(
    text: str,
    kind: str,
    *,
    reference_year: int | None = None,
) -> str:
    dated_lead = re.match(
        r"\s*(?:title:\s*)?(?:in|during)\s+((?:19|20)\d{2})\b",
        text,
        re.I,
    )
    if (
        dated_lead
        and reference_year is not None
        and int(dated_lead.group(1)) < reference_year
    ):
        return "historical"
    dated_event = re.search(
        r"\b(?:had |initially )?(?:receiv(?:e[sd]?|ing)|obtain(?:s|ed|ing)?|"
        r"secur(?:e[sd]?|ing)|was granted|approv(?:e[sd]|ing)|launch(?:e[sd]?|ing)|"
        r"acquir(?:e[sd]?|ing)|fil(?:e[sd]?|ing))\b.{0,120}"
        r"\b(?:in|during)\s+((?:19|20)\d{2})\b",
        text,
        re.I,
    )
    if (
        dated_event
        and reference_year is not None
        and int(dated_event.group(1)) < reference_year
    ):
        return "historical"
    dated_regulatory_state = re.search(
        r"\b((?:19|20)\d{2})\s+(?:complete response letter|clinical hold|"
        r"refusal[- ]to[- ]file letter|action letter)\b",
        text,
        re.I,
    )
    current_response_prefix = bool(
        dated_regulatory_state
        and (
            re.search(
                r"\b(?:fil(?:e[sd]?|ing)|submit(?:s|ted|ting)?|accept(?:s|ed|ing)?|"
                r"acknowledg(?:e[sd]?|ing))\b.{0,120}\b(?:complete )?response\b",
                text[:dated_regulatory_state.start()],
                re.I,
            )
            or re.search(
                r"\b(?:did not consider|does not consider|will not begin|"
                r"refus(?:e[sd]?|ing)|reject(?:s|ed|ing)?|not acceptable)\b"
                r".{0,160}\b(?:application|resubmission|response|review)\b",
                text[:dated_regulatory_state.start()],
                re.I,
            )
        )
    )
    if (
        dated_regulatory_state
        and not current_response_prefix
        and reference_year is not None
        and int(dated_regulatory_state.group(1)) < reference_year
    ):
        return "historical"
    historical_cue = re.search(
        r"\b(?:previously|formerly|earlier(?!\s+(?:today|this))|"
        r"last (?:year|quarter|month)|prior (?:year|quarter|month)|"
        r"had\s+(?:expected|projected|forecast|guided|cut|raised|lowered|reported|posted)|"
        r"historically(?=\s*(?:,|reported\b|posted\b|had\b|was\b|were\b|did\b|"
        r"lost\b|cut\b|fell\b|rose\b|grew\b|declined\b|increased\b|decreased\b|"
        r"underperformed\b|outperformed\b)))",
        text,
        re.I,
    )
    comparator = re.search(
        r"\b(?:vs\.?|versus|compared (?:with|to)|up from|down from)\b",
        text,
        re.I,
    )
    metric = r"(?:EPS|earnings|revenues?|sales|net income|profit|net loss|losses?)"
    dated_metric = re.search(
        rf"\b(?:(?:FY\s*)?((?:19|20)\d{{2}})\s+(?:adjusted\s+|diluted\s+)?{metric}|"
        rf"{metric}\s+(?:for|in|during)\s+((?:19|20)\d{{2}}))\b",
        text,
        re.I,
    )
    dated_metric_year = next(
        (int(value) for value in dated_metric.groups() if value),
        None,
    ) if dated_metric else None
    if (
        dated_metric
        and reference_year is not None
        and dated_metric_year is not None
        and dated_metric_year < reference_year
        and not re.search(
            r"\b(?:reports?|reported|posts?|posted|announces?|announced|releases?|released)\b"
            r".{0,80}$",
            text[:dated_metric.start()],
            re.I,
        )
    ):
        return "historical"
    dated_forecast = re.search(
        r"\b(?:expects?|expected|projects?|projected|forecast|guided)\b"
        r".{0,120}\b(?:in|for|during)\s+((?:19|20)\d{2})\b",
        text,
        re.I,
    )
    if (
        kind == "forecast"
        and reference_year is not None
        and dated_forecast
        and int(dated_forecast.group(1)) < reference_year
    ):
        return "historical"
    current_forecast_revision = bool(
        kind == "forecast"
        and historical_cue
        and (
            re.search(
                r"\b(?:now\s+)?(?:cuts?|cut|raises?|raised|lowers?|lowered|updates?|updated|"
                r"revises?|revised|withdraws?|withdrew|reaffirms?|reaffirmed)\b",
                text[:historical_cue.start()],
                re.I,
            )
            and re.search(
                r"\b(?:guidance|outlook|forecast|range|projection)\b",
                text[historical_cue.start():],
                re.I,
            )
            or re.search(
                r"\bnow\s+(?:expects?|projects?|forecasts?|sees)\b",
                text[:historical_cue.start()],
                re.I,
            )
            and re.search(
                r"\b(?:guidance|outlook|forecast|range|projection)\b",
                text[historical_cue.start():],
                re.I,
            )
        )
    )
    if historical_cue and (
        re.match(r"\s*(?:title:\s*)?(?:previously|earlier|formerly|historically)\b", text, re.I)
        or (kind == "forecast" and not current_forecast_revision)
    ):
        return "historical"
    if kind == "forecast" or re.search(r"\b(?:will|expects?|next year|future|project(?:s|ed|ing|ion)s?|evaluat(?:e|es|ed|ing)|consider(?:s|ed|ing)?|explor(?:e|es|ed|ing)|attempt(?:s|ed|ing)?)\b", text, re.I): return "forward"
    # A current reported result remains current when its comparator is a prior
    # period; the historical phrase qualifies the baseline, not the event.
    if (
        _reported_period_comparison_direction(_normalize_alias(text)) is not None
        or re.search(
            r"\b(?:EPS|earnings per share|revenues?|sales|net income|profit|net loss|losses?)\b"
            r".{0,100}\b(?:up|down|higher|lower|increased|decreased|rose|fell|grew|declined)\b"
            r".{0,80}\b(?:year earlier|same (?:period|quarter) last year|year[- ]over[- ]year|from)\b",
            text,
            re.I,
        )
    ):
        return "current"
    # Historical words frequently modify an antecedent, population, or
    # comparator rather than the governing event. Keep a later completed or
    # continuing action current instead of filtering the whole span.
    if historical_cue and (
        re.search(r"\b(?:from earlier|earlier today|last night)\b", text, re.I)
        or re.search(
            r"\b(?:approved?|reports?|reported|announces?|announced|presents?|presented|"
            r"continu(?:e|es|ed|ing)|prices?|priced|closes?|closed|completes?|completed|"
            r"terminates?|terminated|increases?|increased|decreases?|decreased|rose|fell|grew|declined)\b"
            r".{0,100}\bpreviously\s+(?:treated|untreated|announced|filed|placed|explored)\b",
            text,
            re.I,
        )
        or re.search(
            r"\bpreviously\s+(?:announced|filed|placed|explored)\b.{0,100}"
            r"\b(?:is|are|was|were|has been|have been)?\s*"
            r"(?:priced|closed|completed|terminated|continued|presented)\b",
            text,
            re.I,
        )
        or re.search(
            r"\b(?:has|have|had)?\s*(?:expanded|extended|renewed|increased|completed)\b"
            r".{0,100}\bpreviously[- ]announced\b",
            text,
            re.I,
        )
    ):
        return "current"
    if comparator and re.search(
        r"\b(?:reports?|reported|posts?|posted|announces?|announced)\b.{0,140}"
        r"\b(?:EPS|earnings|revenues?|sales|net income|profit|net loss|losses?)\b",
        text,
        re.I,
    ):
        return "current"
    if comparator and re.search(
        r"\b(?:loss(?:es)?|net loss)\b.{0,80}\b(?:narrowed|reduced|improved)\b|"
        r"\b(?:narrowed|reduced|improved)\b.{0,80}\b(?:loss(?:es)?|net loss)\b",
        text,
        re.I,
    ):
        return "current"
    if historical_cue:
        return "historical"
    return "current"


def _reference_year(value: Any) -> int | None:
    match = re.match(r"\s*((?:19|20)\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _equivalent_prior_event(
    rule: ConceptRule,
    title_event_text: str,
    entity: Mapping[str, Any],
    statements: Sequence[Mapping[str, Any]],
    participations: Sequence[Mapping[str, Any]],
    *,
    reference_year: int | None,
) -> bool:
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    title_time = _time_relation(
        title_event_text,
        rule.statement_kind,
        reference_year=reference_year,
    )
    entity_id = str(entity.get("entity_id") or "")
    for participation in participations:
        if str(participation.get("entity_id") or "") != entity_id:
            continue
        statement = statement_by_id.get(str(participation.get("statement_id") or ""), {})
        if (
            statement.get("concept_leaf") != rule.concept
            or statement.get("time_relation") != title_time
        ):
            continue
        body_text = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        if _event_text_overlap(title_event_text, body_text):
            return True
    return False


def _event_text_overlap(left: str, right: str) -> bool:
    distinct_left = set(re.findall(r"\b(?:another|separate|additional)\b", left, re.I))
    distinct_right = set(re.findall(r"\b(?:another|separate|additional)\b", right, re.I))
    if bool(distinct_left) != bool(distinct_right):
        return False
    terminal_pattern = r"\b(?:withdraw(?:s|n|ing)?|cancel(?:s|led|ing)?|reopen(?:s|ed|ing)?|clos(?:e|es|ed|ing))\b"
    terminal_left = {value.casefold() for value in re.findall(terminal_pattern, left, re.I)}
    terminal_right = {value.casefold() for value in re.findall(terminal_pattern, right, re.I)}
    if terminal_left != terminal_right and (terminal_left or terminal_right):
        return False
    for extractor in (
        lambda value: set(re.findall(
            r"\b(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th))\b",
            value,
            re.I,
        )),
        lambda value: set(re.findall(
            r"(?:[$£€]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:thousand|million|billion))?)",
            value,
            re.I,
        )),
        lambda value: set(re.findall(
            r"\b(?:separate|another|additional|new|launch(?:es|ed|ing)?|"
            r"reopen(?:s|ed|ing)?|prices?|files?|complet(?:e|es|ed|ing)|"
            r"clos(?:e|es|ed|ing)|announces?)\b",
            value,
            re.I,
        )),
        lambda value: set(re.findall(
            r"\b\d+(?:\.\d+)?[-\s]*(?:thousand|million|billion)?[-\s]*"
            r"(?:shares?|units?|securities)\b",
            value,
            re.I,
        )),
        lambda value: set(re.findall(
            r"\b(?:today|yesterday|monday|tuesday|wednesday|thursday|friday|"
            r"january|february|march|april|may|june|july|august|september|"
            r"october|november|december|(?:19|20)\d{2})\b",
            value,
            re.I,
        )),
    ):
        left_values = {item.casefold() for item in extractor(left)}
        right_values = {item.casefold() for item in extractor(right)}
        if left_values and right_values and left_values != right_values:
            return False
    stop = frozenset((
        "a", "an", "and", "at", "by", "for", "from", "in", "is", "of",
        "on", "the", "to", "will", "with",
    ))

    def tokens(value: str) -> set[str]:
        result: set[str] = set()
        for token in re.findall(r"[a-z0-9]+", _normalize_alias(value)):
            if token in stop or len(token) < 3:
                continue
            for suffix in ("ing", "ed", "es", "s"):
                if token.endswith(suffix) and len(token) > len(suffix) + 3:
                    token = token[:-len(suffix)]
                    break
            result.add(token)
        return result

    left_tokens = tokens(left)
    right_tokens = tokens(right)
    common = left_tokens & right_tokens
    return len(common) >= 2 and len(common) * 2 >= min(len(left_tokens), len(right_tokens))


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
        r"\b(?:lift(?:s|ed|ing)?|lifting|remov(?:e[sd]?|ing)|removal|resolv(?:e[sd]?|ing))\b"
        r".{0,80}\bclinical hold\b|\bclinical hold\b.{0,80}"
        r"\b(?:lift(?:s|ed|ing)?|lifting|remov(?:e[sd]?|ing)|removal|resolv(?:e[sd]?|ing))\b|"
        r"\b(?:clearance|permission|authorization)\b.{0,100}\b(?:resume|begin)\b.{0,60}\b(?:trial|study|enrollment)\b|"
        r"\b(?:resume|begin)\b.{0,60}\b(?:trial|study|enrollment)\b.{0,100}\b(?:clearance|permission|authorization)\b",
        normalized_text,
    ))


def _terminal_event_sentiment(
    text: str,
    concept: str,
    entity: Mapping[str, Any] | None,
    mention_terms: Sequence[str],
) -> tuple[str, int] | None:
    """Classify a current terminal transition before adverse-noun fallback.

    The polarity belongs to the governing predicate and economic role: a
    removed hold, dismissed claim, concluded investigation, repaid debt, or
    received settlement is not a fresh hold, claim, investigation, financing,
    or payment burden merely because that adverse noun remains in the text.
    """
    normalized = text.casefold()
    if concept in {"clinical.regulatory_milestone", "regulatory.action", "legal.proceeding"}:
        if concept in {"clinical.regulatory_milestone", "regulatory.action"} and re.search(
            r"\b(?:seek(?:s|ing)?|request(?:s|ed|ing)?|ask(?:s|ed|ing)?|"
            r"plans?|intends?|attempt(?:s|ed|ing)?|continues? to work)\b.{0,140}"
            r"\b(?:lift(?:ing)?|removal|remove|resolve|resume)\b.{0,80}\bclinical hold\b|"
            r"\bpotential lift\b.{0,80}\bclinical holds?\b",
            normalized,
        ):
            # The request itself is not a favorable disposition; the named
            # hold remains the current state until the authority lifts it.
            return "negative", 4
        if _is_resolved_clinical_hold(normalized):
            return "positive", 3
        if concept in {"clinical.regulatory_milestone", "regulatory.action"} and re.search(
            r"\b(?:exchange|amex|nyse|nasdaq)\b.{0,100}"
            r"\b(?:lift(?:s|ed|ing)?|lifting|remov(?:e[sd]?|ing)|removal)\b"
            r".{0,80}\btrading halt\b|"
            r"\b(?:lift(?:s|ed|ing)?|lifting|remov(?:e[sd]?|ing)|removal)\b"
            r".{0,80}\btrading halt\b",
            normalized,
        ):
            return "positive", 2
        if concept in {"clinical.regulatory_milestone", "regulatory.action"} and not re.search(
            r"\b(?:did not|does not|not|refus(?:e[sd]?|ing))\b.{0,60}"
            r"\b(?:consider|accept|acknowledge|agree)\w*\b.{0,80}"
            r"\b(?:complete response|class 2 resubmission)\b",
            normalized,
        ) and re.search(
            r"\b(?:fda|ema)\b.{0,160}"
            r"\b(?:acknowledg(?:e[sd]?|ing)|agree(?:s|d)?|accept(?:s|ed|ance)?|"
            r"consider(?:s|ed|ing)?)\b.{0,140}"
            r"\b(?:complete class 2 response|complete response|class 2 resubmission)\b|"
            r"\b(?:complete class 2 response|class 2 resubmission)\b.{0,140}"
            r"\b(?:acknowledg(?:e[sd]?|ing)|agree(?:s|d)?|accept(?:s|ed|ance)?|"
            r"consider(?:s|ed|ing)?)\b",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\breject(?:s|ed|ing)\b.{0,100}\bpetition\b.{0,100}"
            r"\b(?:open|begin|initiate)\b.{0,50}\binvestigation\b|"
            r"\bpetition\b.{0,100}\b(?:open|begin|initiate)\b.{0,50}"
            r"\binvestigation\b.{0,100}\breject(?:s|ed|ing)\b",
            normalized,
        ):
            return "positive", 2
        if concept == "legal.proceeding" and re.search(
            r"\bfil(?:e[sd]?|ing)\b.{0,80}\bcomplaint\b.{0,80}"
            r"\bagainst\b.{0,80}\b(?:fda|ema|regulator|agency)\b.{0,140}"
            r"\b(?:request(?:s|ed|ing)?|seek(?:s|ing)?)\b.{0,80}"
            r"\b(?:lift|remove|resolve)\b.{0,60}\bclinical hold\b",
            normalized,
        ):
            return "positive", 2
        if concept in {"clinical.regulatory_milestone", "regulatory.action"} and re.search(
            r"\bresubmit(?:s|ted|ting)?\b.{0,160}"
            r"\b(?:after|having)\b.{0,100}\b(?:address(?:es|ed|ing)?|resolv(?:e[sd]?|ing))\b"
            r".{0,100}\b(?:complete response letter|crl|observations?|issues?)\b|"
            r"\b(?:address(?:es|ed|ing)?|resolv(?:e[sd]?|ing))\b.{0,100}"
            r"\b(?:complete response letter|crl|observations?|issues?)\b.{0,160}"
            r"\bresubmit(?:s|ted|ting)?\b",
            normalized,
        ):
            return "positive", 2
    if concept == "capital.financing" and re.search(
        r"\b(?:repaid|has repaid|have repaid|paid off|fully paid|final payment|completion of (?:the )?repayment)\b"
        r".{0,120}\b(?:convertible|senior)?\s*(?:notes?|debt|obligations?)\b|"
        r"\b(?:convertible|senior)?\s*(?:notes?|debt|obligations?)\b.{0,120}"
        r"\b(?:repaid|has repaid|have repaid|paid off|fully paid|final payment)\b",
        normalized,
    ):
        return "positive", 3
    if concept in {"legal.proceeding", "regulatory.action"}:
        if concept == "legal.proceeding" and re.search(
            r"\bdismiss(?:es|ed) as moot\b.{0,120}\b(?:action|case|claim)\b|"
            r"\b(?:action|case|claim)\b.{0,120}\bdismiss(?:es|ed) as moot\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:investigations?|inquiries|probes?)\b\s*"
            r"(?:has|have|is|are|was|were)?\s*(?:conclud(?:e[sd]?|ing)|clos(?:e[sd]?|ing)|ended?)\b|"
            r"\b(?:concludes?|closes?|ends?)\b.{0,30}\b(?:both\s+)?"
            r"(?:investigations?|inquiries|probes?)\b|"
            r"\b(?:not to recommend|no)\b.{0,50}\bcharges?\b",
            normalized,
        ):
            return "positive", 2
        dismissed_claim = re.search(
            r"\b(?:class action|lawsuit|complaint|legal claim)\b.{0,100}"
            r"\bdismiss(?:es|ed|al)?\b|\bdismiss(?:es|ed|al)?\b.{0,100}"
            r"\b(?:class action|lawsuit|complaint|legal claim)\b",
            normalized,
        )
        if dismissed_claim and entity is not None:
            claim = re.search(r"\b(?:class action|lawsuit|complaint|legal claim)\b", text, re.I)
            if claim and re.search(r"(?:'s|’s|['’])\s*$", text[:claim.start()], re.I) and _entity_in_quote(
                entity,
                text[max(0, claim.start() - 100):claim.start()],
                mention_terms,
            ):
                return "negative", 2
        if dismissed_claim and not re.search(
            r"\b(?:files?|filed|brings?|brought)\b.{0,80}"
            r"\b(?:lawsuit|complaint|claim)\b.{0,120}\bdismiss(?:es|ed|al)?\b",
            normalized,
        ):
            return "positive", 2
        payer = re.search(
            r"\b(?:pays?|paid|paying|makes?|made)\b.{0,80}"
            r"\b(?:payment|settlement|penalty|fine)\b|"
            r"\bpayment\b.{0,60}\bby\b",
            normalized,
        )
        if payer and (
            entity is None
            or _entity_in_quote(entity, text[:payer.end() + 100], mention_terms)
            or re.search(r"\b(?:the company|it)\b", text[:payer.end() + 100], re.I)
        ):
            return "negative", 3
        receipt = re.search(
            r"\b(?:will\s+)?receiv(?:e[sd]?|ing)\b.{0,100}"
            r"\b(?:payment|proceeds?|settlement)\b|"
            r"\bpayment\b.{0,80}\bto\b|"
            r"\b(?:after[- ]tax income|settlement income)\b",
            normalized,
        )
        if receipt:
            if entity is None:
                return "positive", 3
            role_window = text[max(0, receipt.start() - 180):receipt.end() + 120]
            payment_to = re.search(r"\bpayment\b.{0,80}\bto\b", role_window, re.I)
            against = re.search(r"\bagainst\b", text[:receipt.start()], re.I)
            counterparty = bool(
                against
                and _entity_in_quote(
                    entity,
                    text[against.end():receipt.start()],
                    mention_terms,
                )
            )
            if (
                not counterparty
                and (
                (
                    re.search(r"\breceiv(?:e[sd]?|ing)\b", role_window, re.I)
                    and _entity_in_quote(entity, role_window, mention_terms)
                )
                or (
                    re.search(r"\b(?:after[- ]tax income|settlement income)\b", role_window, re.I)
                    and _entity_in_quote(entity, text, mention_terms)
                )
                or (
                    payment_to is not None
                    and (
                        _entity_in_quote(
                            entity,
                            role_window[payment_to.end():payment_to.end() + 100],
                            mention_terms,
                        )
                        or re.search(
                            r"\b(?:the company|it)\b",
                            role_window[payment_to.end():payment_to.end() + 100],
                            re.I,
                        )
                    )
                )
                )
            ):
                return "positive", 3
    return None


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
    terminal_sentiment = _terminal_event_sentiment(
        text,
        rule.concept,
        entity,
        mention_terms,
    )
    if terminal_sentiment is not None:
        return terminal_sentiment
    if (
        rule.concept == "operations.cost_efficiency"
        and re.search(
            r"\b(?:lack|absence) of\b.{0,60}\b(?:cost )?savings?\b|"
            r"\bno\b.{0,40}\b(?:cost )?savings?\b",
            normalized,
        )
        and (
            entity is None
            or _entity_in_quote(entity, text, mention_terms)
        )
    ):
        return "negative", 2
    if rule.concept == "analyst.rating_action":
        rating_counts = {
            label.casefold(): int(count)
            for label, count in re.findall(
                r"\b(bullish|somewhat bullish|indifferent|somewhat bearish|bearish)\s*=\s*(\d+)",
                text,
                re.I,
            )
        }
        bullish = rating_counts.get("bullish", 0) + rating_counts.get("somewhat bullish", 0)
        bearish = rating_counts.get("bearish", 0) + rating_counts.get("somewhat bearish", 0)
        if bullish > bearish:
            return "positive", 3 if bullish >= 3 else 2
        if bearish > bullish:
            return "negative", 3 if bearish >= 3 else 2
        if re.search(r"\bdowngrad\w*\b", normalized): return "negative", 3
        if re.search(r"\bupgrad\w*\b", normalized): return "positive", 3
        if re.search(r"\b(?:sell|underperform|underweight)\b", normalized): return "negative", 2
        if re.search(r"\b(?:buy|outperform|overweight)\b", normalized): return "positive", 2
        return "neutral", 0
    if rule.concept == "analyst.price_target_action":
        target_term = r"(?:price target|target price|price objective|PT|PO|P/T|\$\d+(?:\.\d+)? target)"
        if re.search(
            rf"\b(?:cuts?|lowers?|reduc(?:e|es|ed))\b.{{0,60}}\b{target_term}\b|"
            rf"\b{target_term}\b.{{0,60}}\b(?:cut|lowered?|reduced?)\b",
            normalized,
            re.I,
        ):
            return "negative", 2
        if re.search(
            rf"\b(?:rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost(?:s|ed|ing)?)\b.{{0,60}}\b{target_term}\b|"
            rf"\b{target_term}\b.{{0,60}}\b(?:raised?|increased?|boosted?)\b",
            normalized,
            re.I,
        ):
            return "positive", 2
        if re.search(
            rf"\b(?:maintain(?:s|ed|ing)?|reiterate(?:s|d|ing)?|unchanged)\b.{{0,60}}\b{target_term}\b|"
            rf"\b{target_term}\b.{{0,60}}\b(?:maintain(?:s|ed|ing)?|reiterate(?:s|d|ing)?|unchanged)\b",
            normalized,
            re.I,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:current )?average\b.{0,40}\b(?:decreased|declined|fell)\b"
            r".{0,80}\bprevious average price target\b",
            normalized,
        ):
            return "negative", 1
        return "neutral", 0
    if rule.concept == "analyst.issuer_assessment":
        if (
            re.search(
                r"\b(?:no longer|not)\s+(?:bullish|positive|a buyer)\b|"
                r"\b(?:is not|isn't|was not|wasn't)\s+a buyer\b|"
                r"\b(?:not willing|unable|declines?)\s+to\s+(?:recommend|endorse)\b|"
                r"\b(?:cannot|can't|does not|doesn't)\s+(?:recommend|endorse)\b",
                normalized,
            )
            and (
                entity is None
                or _entity_in_quote(entity, text, mention_terms)
            )
        ):
            return "negative", 2
        if re.search(
            r"\b(?:case|claim|lawsuit|action|investigation)\b.{0,100}"
            r"\b(?:difficult|unlikely)\b.{0,40}\b(?:to pursue|to prove|to win|to succeed)\b|"
            r"\b(?:case|claim|lawsuit|action|investigation)\b.{0,120}\bpolitical(?:ly motivated| calculation)?\b|"
            r"\b(?:political(?:ly motivated)?|without merit|baseless)\b.{0,100}"
            r"\b(?:case|claim|lawsuit|action|investigation)\b",
            normalized,
        ):
            return "positive", 2
    if rule.concept == "analyst.short_thesis":
        if re.search(r"\b(?:scam\w*|fraud\w*|short thesis|short report|bear thesis)\b", normalized):
            return "negative", 3
        return "negative", 2
    if rule.concept == "estimate.revision":
        relations = {
            str(fact.get("relation"))
            for fact in typed_facts
            if fact.get("fact_type") == "estimate_comparison"
            and fact.get("subject_role") == "analyst_estimate"
            and fact.get("comparator_role") == "consensus_estimate"
        }
        if "below" in relations and "above" not in relations:
            return "negative", 2
        if "above" in relations and "below" not in relations:
            return "positive", 2
        range_positions = {
            str(fact.get("position"))
            for fact in typed_facts
            if fact.get("fact_type") == "estimate_range_position"
            and fact.get("subject_role") == "analyst_estimate"
        }
        if "low_end" in range_positions and "high_end" not in range_positions:
            return "negative", 1
        revision_directions = {
            str(fact.get("direction"))
            for fact in typed_facts
            if fact.get("fact_type") == "estimate_revision"
        }
        if revision_directions == {"up"}:
            return "positive", 1
        if revision_directions == {"down"}:
            return "negative", 2
        return "neutral", 0
    if rule.concept == "capital.financing":
        if re.search(r"\bprices?\b.{0,80}\b(?:ADS|ADRs?)\b", text, re.I) and not re.search(
            r"\b(?:offering|dilution|warrants?|convertible)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\bregister(?:s|ed|ing)?\b.{0,80}\bshares?\b.{0,80}"
            r"\bissuable\b.{0,80}\bwarrant exercise\b",
            normalized,
        ):
            return "negative", 2
        secondary_holder_sale = re.search(
            r"\b(?:selling|existing) (?:stockholders?|shareholders?|holders?)\b|"
            r"\bsecondary offering\b",
            normalized,
        )
        issuer_primary_tranche = re.search(
            r"\b(?:the\s+)?(?:company|issuer|we)\s+"
            r"(?:(?:is|are|will|would|has|have)\s+)?"
            r"(?:(?:offer(?:s|ed)?|offering)|issu(?:e|es|ed|ing)|sell(?:s|ing)?)\b"
            r".{0,80}\b(?:new|newly issued|common|preferred)?\s*"
            r"(?:stock|shares?|units?|securities)\b|"
            r"\b(?:new|newly issued)\s+shares?\b|"
            r"\bshares? offered by (?:the )?(?:company|issuer|us)\b",
            normalized,
        )
        issuer_no_proceeds = re.search(
            r"\b(?:will|would|does|did) not receive\b.{0,80}\bproceeds\b|"
            r"\breceiv(?:e|es|ed|ing) no proceeds\b|"
            r"\bno proceeds\b.{0,80}\b(?:company|issuer|us|we)\b",
            normalized,
        )
        if secondary_holder_sale and issuer_no_proceeds and not issuer_primary_tranche:
            return "neutral", 0
        if re.search(r"\b(?:initial public offering|IPO)\b", normalized):
            ipo_role = _subsidiary_ipo_role(entity, text, mention_terms)
            if ipo_role == "parent":
                return "positive", 2
            if ipo_role == "unrelated":
                return "neutral", 0
            if re.search(r"\babove\b.{0,50}\b(?:expected )?(?:price )?range\b", normalized):
                return "positive", 3
            return "negative", 2
        if re.search(r"\b(?:debt|senior notes?|bonds?)\b", normalized) and not re.search(r"\bconvertible\b", normalized):
            return "neutral", 0
        if re.search(
            r"\b(?:will offer|offers?)\b.{0,80}\b(?:common|preferred)?\s*shares\b|"
            r"\b(?:offering|sale) of\b.{0,80}\b(?:common|preferred)?\s*(?:stock|shares|units?|warrants?|securities)\b|"
            r"\b(?:common|preferred)?\s*(?:stock|shares)\b.{0,50}\boffering\b",
            normalized,
        ):
            return "negative", 3
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
            and fact.get("comparator_role") in {
                "consensus_estimate",
                "management_guidance",
            }
        }
        if "below" in relations and "above" not in relations:
            return "negative", 3
        if "above" in relations and "below" not in relations:
            return "positive", 3
        if relations:
            return "neutral", 0
        revision_directions = {
            str(fact.get("direction"))
            for fact in typed_facts
            if fact.get("fact_type") == "estimate_revision"
        }
        if "down" in revision_directions and "up" not in revision_directions:
            return "negative", 3
        if "up" in revision_directions and "down" not in revision_directions:
            return "positive", 3
        if re.search(
            r"\b(?:comparable[- ]store sales|same[- ]store sales|comps?|comp)\b"
            r".{0,80}\bnegative\b|\bnegative\b.{0,80}"
            r"\b(?:comparable[- ]store sales|same[- ]store sales|comps?|comp)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:profit|EPS|earnings|revenue|sales)\s+(?:forecast|outlook|guidance)\b"
            r".{0,120}\b(?:fell short of|below|miss(?:es|ed)?)\b.{0,60}"
            r"\b(?:the )?(?:street|consensus|analysts?'? estimates?|view)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:negative impact|decline|decrease)\b.{0,100}\b(?:EPS|earnings|revenue|sales|growth|margin)\b|"
            r"\b(?:EPS|earnings|revenue|sales|growth|margin)\b.{0,100}\b(?:negative impact|decline|decrease)\b",
            normalized,
        ):
            return "negative", 3
        explicitly_incorrect_prior_raise = re.search(
            r"\b(?:prior|earlier|previous)\b.{0,100}\b(?:headline|report|statement)\b"
            r".{0,120}\brais(?:e|es|ed|ing)\b.{0,80}\b(?:guidance|outlook|forecast)\b"
            r".{0,100}\b(?:incorrect|inaccurate|wrong|error)\b",
            normalized,
        )
        if not explicitly_incorrect_prior_raise and re.search(r"\brais(?:e|es|ed|ing)\b.{0,80}\b(?:guidance|outlook|forecast)\b", normalized):
            return "positive", 3
        if re.search(r"\b(?:cuts?|lowers?|reduc(?:e|es|ed|ing)|withdraws?|suspends?)\b.{0,60}\b(?:guidance|outlook|forecast)\b", normalized):
            return "negative", 3
        if re.search(
            r"\b(?:not providing|declines? to provide|will not provide|withholds?)\b"
            r".{0,80}\bguidance\b",
            normalized,
        ):
            return "negative", 2
        if re.search(r"\b(?:raises?|boosts?|increases?)\b.{0,60}\b(?:guidance|outlook|forecast)\b", normalized):
            return "positive", 3
        if (
            re.search(r"\b(?:revenue|sales) growth\b", normalized)
            and not re.search(r"\b(?:decline|decrease|negative|down|slowing|lower)\b.{0,80}\b(?:revenue|sales)?\s*growth\b|\bgrowth\b.{0,80}\b(?:decline|decrease|negative|down|slowing|lower)\b", normalized)
            and _positive_growth_fact(typed_facts)
        ):
            # Positive absolute growth without a market benchmark is favorable,
            # but materially weaker than guidance versus consensus.
            return "positive", 1
        if (
            re.search(r"\b(?:sees|expects?|targets?|projects?)\b.{0,100}\bgrowth\b.{0,80}\b(?:revenue|sales)\b", normalized)
            and _positive_growth_fact(typed_facts)
        ):
            return "positive", 1
    if rule.concept in {"clinical.regulatory_milestone", "regulatory.action"}:
        if re.search(
            r"\b(?:did not|does not|will not)\b.{0,100}"
            r"\b(?:consider|accept|begin)\b.{0,100}"
            r"\b(?:complete response|substantive review|application|resubmission)\b|"
            r"\b(?:not acceptable|not approvable|unable to approve)\b",
            normalized,
        ):
            return "negative", 4
        if re.search(
            r"\b(?:fda|ema|regulator\w*|agency)\b.{0,80}"
            r"\bissu(?:e[sd]?|ing)\b.{0,40}\bresponse letter\b",
            normalized,
        ) and not re.search(
            r"\b(?:complete response letter|refusal[- ]to[- ]file|"
            r"accept(?:s|ed)|acknowledg(?:e[sd]?)|approv(?:es|ed)|"
            r"reject(?:s|ed)|not acceptable|unable to approve)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:fda|ema|regulator\w*)\b.{0,120}"
            r"\b(?:accept(?:s|ed|ance)?|acknowledg(?:e[sd]?|ing))\b.{0,100}"
            r"\b(?:complete )?(?:response|resubmission|submission|application)\b|"
            r"\bconsiders?\b.{0,80}\b(?:complete|class\s*2)\s+response\b",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:plans?|intends?|expects?)\b.{0,80}\b(?:seek|file|submit)\b"
            r".{0,100}\b(?:fda|ema)\b.{0,80}\b(?:priority review|snda|nda|bla|application)\b",
            normalized,
        ):
            return "positive", 1
        if re.search(
            r"\b(?:withdraw(?:s|n|al)?|withdrew)\b.{0,100}"
            r"\b(?:NDA|BLA|new drug application|application|submission)\b|"
            r"\b(?:NDA|BLA|new drug application|application|submission)\b"
            r".{0,100}\b(?:withdraw(?:s|n|al)?|withdrew)\b",
            text,
            re.I,
        ):
            return "negative", 3
        if re.search(
            r"\bfil(?:e[sd]?|ing)\b.{0,100}\b(?:response|submission|application)\b"
            r".{0,80}\b(?:FDA|EMA)\b|"
            r"\b(?:response|submission|application)\b.{0,80}\b(?:filed|submitted)\b"
            r".{0,80}\b(?:FDA|EMA)\b",
            text,
            re.I,
        ):
            return "positive", 2
        if rule.concept == "regulatory.action" and re.search(
            r"\benter(?:s|ed|ing)?\b.{0,80}\bpre[- ]filing review process\b"
            r".{0,120}\b(?:FERC|Federal Energy Regulatory Commission)\b",
            text,
            re.I,
        ):
            return "positive", 1
        if rule.concept == "regulatory.action" and re.search(
            r"\b(?:trading halt|halted)\b.{0,80}\bnews pending\b",
            normalized,
        ) and not re.search(
            r"\b(?:investigation|non[- ]?compliance|delist|fraud|subpoena)\b",
            normalized,
        ):
            return "neutral", 0
        # A regulator's adverse disposition is the controlling event even when
        # the same sentence names the approval being sought. Remediation scope,
        # management confidence, and approvals of separate application
        # components remain independent evidence rather than canceling it.
        if re.search(
            r"\b(?:upon|following|subject to|conditioned on|if|after)\b.{0,100}"
            r"\b(?:FDA\s+)?(?:approval|clearance|authorization)\b",
            normalized,
        ) and not re.search(
            r"\b(?:FDA|EMA|regulator\w*)\b.{0,80}"
            r"\b(?:granted|approved|cleared|authorized)\b|"
            r"\b(?:receiv(?:e[sd]?|ing)|obtain(?:s|ed|ing)?|secur(?:e[sd]?|ing))\b.{0,50}"
            r"\b(?:approval|clearance|authorization)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(r"\bfast track designation\b", normalized):
            return "neutral", 0
        if re.search(
            r"\b(?:FDA|EMA|regulator\w*)\b.{0,100}\bexpand\w*\b.{0,60}\bindication\b",
            normalized,
        ):
            return "positive", 3
        if re.search(
            r"\bawait(?:s|ed|ing)?\b.{0,100}\b(?:decision|approval|clearance|authorization)\b|"
            r"\b(?:committee|panel)\b.{0,80}\bwill discuss\b|"
            r"\bpropos(?:e[sd]?|ing)\b.{0,100}\b(?:treatment|convert|approval|clearance)\b|"
            r"\b(?:docket|public comments?|comments? submitted|meeting materials?|"
            r"background materials?)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:could|may|might|eventual|potential|even with)\b.{0,100}"
            r"\b(?:approval|clearance|authorization)\b|"
            r"\b(?:approval|clearance|authorization)\b.{0,100}\b(?:could|may|might|eventual|potential)\b",
            normalized,
        ) and not re.search(
            r"\b(?:granted|received|obtained|cleared|approved|acknowledged|accepted|lifted|removed)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:prepare(?:s|d|ing)? to|plans? to|continues? to|working to|"
            r"collaborat(?:e[sd]?|ing)(?: with .{0,50})? to)\b"
            r".{0,100}\b(?:respond to|address|resolve|complete)\b.{0,100}"
            r"\b(?:complete response letter|CRL|questions?|issues?|remediation activities)\b|"
            r"\b(?:CRL|complete response letter)\b.{0,80}\bremediation activities\b|"
            r"\bfollowing\b.{0,100}\bcomplete response letter\b.{0,100}"
            r"\breceiv(?:e[sd]?|ing)\b",
            text,
            re.I,
        ) and not re.search(
            r"\b(?:today|currently|just)\b.{0,80}\b(?:receiv(?:e[sd]?)|issu(?:e[sd]?))\b"
            r".{0,80}\b(?:complete response letter|CRL)\b|"
            r"\b(?:receiv(?:e[sd]?)|issu(?:e[sd]?))\b.{0,80}"
            r"\b(?:complete response letter|CRL)\b.{0,80}\b(?:today|currently|just)\b",
            text,
            re.I,
        ):
            return "neutral", 0
        regulatory_outcomes = {
            str(fact.get("outcome_class"))
            for fact in typed_facts
            if fact.get("fact_type") == "regulatory_decision"
        }
        regulatory_effects = {
            str(fact.get("commercial_effect"))
            for fact in typed_facts
            if fact.get("fact_type") == "regulatory_decision"
            and fact.get("outcome_class") == "favorable"
        }
        if "adverse" in regulatory_outcomes:
            return "negative", 4
        if re.search(
            r"\b(?:myocarditis|pericarditis|adverse event|safety warning|boxed warning)\b",
            normalized,
        ):
            return "negative", 2
        if "favorable" in regulatory_outcomes:
            strength = 2 if regulatory_effects <= {
                "regulatory_review_started",
                "supplement_scope_granted",
            } else 3
            return "positive", strength
        if regulatory_outcomes == {"procedural"}:
            if any(
                fact.get("outcome") == "regulatory_submission"
                for fact in typed_facts
                if fact.get("fact_type") == "regulatory_decision"
            ):
                return "positive", 1 if re.search(
                    r"\b(?:plans?|intends?|expects?|seeks?|attempts?|will)\b.{0,100}\bsubmit\w*\b",
                    normalized,
                ) else 2
            return "neutral", 0
        if re.search(
            r"\b(?:attempt(?:s|ed|ing)?|seek(?:s|ing)?|aim(?:s|ed|ing)?|try(?:ing|ies|ied)?)\b"
            r".{0,100}\b(?:secure|obtain|receive)?\s*(?:FDA\s+)?(?:approval|clearance|authorization)\b",
            normalized,
        ):
            return "neutral", 0
        if _is_resolved_clinical_hold(normalized):
            return "positive", 3
        if _is_adverse_regulatory_response(normalized):
            return "negative", 4
        if rule.concept == "regulatory.action" and re.search(
            r"\b(?:SEC|regulator\w*)\b.{0,120}\b(?:inquiry|investigation|subpoena|requests? information)\b",
            normalized,
        ):
            return "negative", 3
        if rule.concept == "clinical.regulatory_milestone" and re.search(
            r"\b(?:regulatory submission|submit(?:s|ted|ting)?\b.{0,80}\b(?:application|data)|"
            r"fil(?:e|es|ed|ing)\b.{0,80}\b(?:application|data)\b)",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:grant(?:s|ed)?|agree(?:s|d)?)\b.{0,100}\b(?:meet|meeting)\b.{0,80}\bFDA\b|"
            r"\b(?:FDA nod|USDA approval|CE mark|terrestrial authorization)\b",
            normalized,
        ):
            return "positive", 2
    if rule.concept == "clinical.trial_result":
        biosimilar = re.search(
            r"\bdata\b.{0,120}\bshows?\b(?P<subject>.{0,120}\bbiosimilar\b)"
            r".{0,80}\bhighly similar\b.{0,40}\bto\b(?P<comparator>.{0,120})",
            text,
            re.I,
        )
        if biosimilar and entity is not None:
            if _entity_in_quote(entity, biosimilar.group("subject"), mention_terms) or _entity_possessive_in_quote(
                entity,
                biosimilar.group("subject"),
                mention_terms,
            ):
                return "positive", 2
            if _entity_in_quote(entity, biosimilar.group("comparator"), mention_terms) or _entity_possessive_in_quote(
                entity,
                biosimilar.group("comparator"),
                mention_terms,
            ):
                return "negative", 2
        if re.search(
            r"\bpresent(?:s|ed|ing)?\b.{0,100}\bpositive\b.{0,100}"
            r"\bdata\b.{0,100}\bclinical program\b|"
            r"\bpublication of data\b.{0,120}\bshows?\b.{0,120}"
            r"\b(?:induces?|activates?|improves?)\b.{0,140}\blinked to\b"
            r".{0,80}\b(?:prevention|treatment)\b",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:investigator|physician|assessment|review)\b.{0,160}"
            r"\bconclud(?:e[sd]?|ing)\b.{0,100}\bnot (?:a )?case of\b"
            r".{0,120}\brevis(?:e[sd]?|ing) (?:the )?diagnosis\b",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:adverse|unwanted|harmful)\b.{0,60}"
            r"\b(?:immune|immunologic|immunological) response (?:data|results?)\b",
            normalized,
        ):
            return "negative", 2
        if re.search(
            r"\b(?:sample size|study duration|enrollment period|chosen assuming|"
            r"statistical assumptions?|power calculation)\b",
            normalized,
        ) and not re.search(
            r"\b(?:results?|met|failed|did not meet|demonstrated|showed|observed)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:primary|co-primary|secondary) endpoint\b.{0,100}"
            r"\b(?:did not meet|failed to meet|missed)\b.{0,80}"
            r"\b(?:statistical significance|endpoint)?\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:shut(?:s|ting)? down|discontinues?|terminates?)\b.{0,100}"
            r"\b(?:clinical trial|study|phase [123])\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:initiat(?:e[sd]?|ion)|commenc(?:e[sd]?|ement)|begin(?:s|ning)?)\b"
            r".{0,100}\b(?:phase\s*[123][a-z]?\s+)?(?:clinical\s+)?(?:study|trial)\b",
            normalized,
        ):
            return "positive", 2
        if re.search(r"\b(?:did not|failed to)\b.{0,100}\b(?:meet|demonstrate|achieve)\b.{0,80}\b(?:primary )?(?:endpoint|goal|dose[- ]response)\b", normalized):
            return "negative", 3
        if re.search(r"\b(?:met|meets|achieved|demonstrates?)\b.{0,120}\b(?:primary\b.{0,60})?(?:endpoints?|goals?|dose[- ]response)\b|\b(?:topline results?\b.{0,100}\bshowing efficacy|high efficacy|durable viral suppression|statistically significant(?: and clinically meaningful)? improvement|significantly reduces?|prevent(?:s|ed|ing)?\s+\d+(?:\.\d+)? percent|\d+(?:\.\d+)? percent (?:vaccine )?efficacy|overall survival improvement|sustained virologic response)\b", normalized):
            return "positive", 3
        if re.search(
            r"\bstatistically significant (?:immune|immunologic|immunological) response "
            r"(?:data|results?)\b",
            normalized,
        ):
            return "positive", 2
        if re.search(r"\b(?:opens?|initiates?|announces?)\b.{0,80}\b(?:phase [123] )?trial site\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:study|trial) results?\b.{0,80}\bpublished\b|\bpublished\b.{0,80}\b(?:study|trial) results?\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:present|presentation)\w*\b.{0,100}\bupdated (?:data|results?)\b|\bupdated (?:data|results?)\b.{0,100}\b(?:present|presentation)\w*\b", normalized):
            return "positive", 1
        if re.search(r"\bfirst (?:patient|subject) (?:enrolled|dosed)\b|\b(?:enrolls?|doses?) (?:the )?first (?:patient|subject)\b", normalized):
            return "positive", 2
        if re.search(r"\bfirst (?:patient|subject) screened\b|\bscreens? (?:the )?first (?:patient|subject)\b", normalized):
            return "positive", 1
        if re.search(r"\bexpansion of\b.{0,100}\bclinical development program\b.{0,120}\bnew trial\b", normalized):
            return "positive", 2
        if re.search(r"\bpositive\b.{0,80}\bclinical (?:data|results?)\b|\bclinical (?:data|results?)\b.{0,80}\bpositive\b", normalized):
            return "positive", 2
    if rule.concept == "product.milestone" and re.search(
        r"\b(?:evaluat(?:e|es|ed|ing)|consider(?:s|ed|ing)?|explor(?:e|es|ed|ing))\b"
        r".{0,80}\b(?:launch|commercializ|introduc|roll(?:s|ed)? out)\w*\b",
        normalized,
    ):
        return "positive", 1
    if rule.concept == "product.milestone" and re.search(
        r"\b(?:launch(?:es|ed)?|introduc(?:e|es|ed)|commercializ(?:e|es|ed)|"
            r"ships? (?:the )?first|first shipment|deliver(?:s|ed)? (?:the |its )?\d+(?:st|nd|rd|th)?|"
        r"release(?:s|d)? (?:the )?(?:final )?pricing|released?)\b",
        normalized,
    ):
        return "positive", 2
    if rule.concept == "product.milestone" and re.search(
        r"\b(?:products?|goods?|inventory)\b.{0,120}"
        r"\b(?:will be|were|are|was|has been|have been)?\s*returned\b"
        r".{0,100}\b(?:to (?:the )?company|to (?:the )?supplier|by (?:a |the )?"
        r"(?:customer|distributor|retailer|regulator))\b|"
        r"\b(?:customer|distributor|retailer|regulator)\b.{0,100}"
        r"\breturn(?:s|ed|ing)?\b.{0,100}\b(?:products?|goods?|inventory)\b",
        normalized,
    ):
        return "negative", 3
    if (
        rule.concept == "corporate_transaction.asset_sale"
        and _material_cash_consideration(typed_facts)
        and re.search(
        r"\b(?:sell(?:s|ing)?|sale of)\b.{0,160}"
        r"(?:\$\s*\d|\b\d+(?:\.\d+)?\s*(?:million|billion)\b)"
        r".{0,80}\b(?:cash|proceeds?|earnout|consideration)\b",
        text,
        re.I,
        )
    ):
        return "positive", 2
    if rule.concept == "financial.margin" and any(
        fact.get("fact_type") == "operating_risk"
        and fact.get("direction") == "adverse"
        for fact in typed_facts
    ):
        return "negative", 2
    if rule.concept in {"earnings.performance", "financial.operating_performance"}:
        relations = {
            str(fact.get("relation"))
            for fact in typed_facts
            if fact.get("fact_type") == "estimate_comparison"
            and fact.get("subject_role") == "reported_result"
            and fact.get("comparator_role") == "consensus_estimate"
        }
        if "below" in relations and "above" not in relations:
            return "negative", 3
        if "above" in relations and "below" not in relations:
            return "positive", 3
        period_direction = _reported_period_comparison_direction(normalized)
        if period_direction is not None:
            # Equality carries direction information but no directional
            # magnitude.  Returning strength 3 for an unchanged comparison
            # produces an internally invalid neutral participation.
            return period_direction, 0 if period_direction == "neutral" else 3
        if re.search(r"\b(?:disappointing|weak|weaker)\b.{0,60}\b(?:earnings|results?|sales|revenue|profit)\b", normalized):
            return "negative", 3
        if re.search(r"\b(?:sales|revenue|EPS|earnings)\b.{0,40}\bdown\b", normalized):
            # An absolute change amount without the prior-period level or a
            # rate is adverse but weak; it must not cancel a benchmarked beat.
            if re.search(
                r"\b(?:sales|revenue)\b.{0,30}\bdown\b\s*(?:E?\$|Â£|â‚¬)\s*"
                r"\d[\d,]*(?:\.\d+)?\s*(?:million|billion|[MB])?\s+from the same period",
                normalized,
            ):
                return "negative", 1
            return "negative", 3
        if re.search(r"\b(?:profit|income|eps)\b.{0,80}\b(?:as )?(?:compared (?:with|to)|vs\.?)\b.{0,30}\b(?:the |a )?loss\b", normalized):
            return "positive", 3
        if re.search(r"\b(?:eps|earnings per share)\b.{0,30}\$?\s*\(\d+(?:\.\d+)?\)", normalized):
            if re.search(
                r"\b(?:eps|earnings per share)\b.{0,40}\([^)]*\)\s+up from\s+"
                r"(?:[$£€]\s*)?\([^)]*\)",
                normalized,
            ):
                return "positive", 3
            return "negative", 2
        if re.search(r"\bswings? to\b.{0,30}\bprofit\b", normalized):
            return "positive", 3
        if re.search(r"\b(?:earnings|sales) (?:were )?up\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:earnings|sales|losses) (?:were )?down\b", normalized):
            return "negative", 2
        if rule.concept == "earnings.performance" and re.search(r"\bupbeat\b.{0,60}\b(?:earnings|profit|results?)\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:net )?(?:income|profit)\b.{0,60}\b(?:down|drop|declin|fell|decreas)\w*\b|\b(?:down|drop|declin|fell|decreas)\w*\b.{0,60}\b(?:net )?(?:income|profit)\b", normalized):
            return "negative", 3
        if re.search(r"\b(?:slowing|declining|negative)\b.{0,40}\b(?:sales|revenue)?\s*growth\b", normalized):
            return "negative", 2
        if re.search(r"\b(?:narrowed|reduced|cut)\b.{0,40}\b(?:net )?loss\b|\b(?:net )?loss\b.{0,40}\bnarrowed\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:widened|increased)\b.{0,40}\b(?:net )?loss\b|\b(?:net )?loss\b.{0,40}\bwidened\b", normalized):
            return "negative", 2
    if rule.concept == "listing.market_structure":
        unachieved_compliance = re.search(
            r"\b(?:never\s+regained|not(?:\s+(?:yet|fully|successfully)){0,2}\s+regained|"
            r"(?:has|have|had)\s+yet\s+to\s+regain|"
            r"(?:did|does|has|have|had|could|can|was|were)\s+(?:not|n t)"
            r"(?:\s+(?:yet|fully|successfully)){0,2}\s+regain(?:ed)?|"
            r"(?:hasn|haven|hadn|didn|doesn|couldn|wasn|weren)['’]t"
            r"(?:\s+(?:yet|fully|successfully)){0,2}\s+regain(?:ed)?|"
            r"(?:cannot|can't|could not|failed to|fails to)\s+regain|"
            r"(?:is|are|was|were|has been|have been)\s+unable\s+to\s+regain)"
            r"(?:\s+[a-z0-9-]+){0,5}\s+compliance\b",
            normalized,
        )
        later_adverse_state = re.search(
            r"\b(?:regained|regains)\b.{0,80}\bcompliance\b.{0,180}"
            r"\b(?:but|however|before|later|subsequently|then)\b.{0,100}"
            r"\b(?:non[- ]?compliance|deficien(?:cy|t)|delisting|minimum bid notice|"
            r"los(?:t|es)\s+(?:its\s+)?compliance|"
            r"(?:fell|falls|falling)\s+out of compliance|"
            r"(?:fell|falls|falling)\s+below\b.{0,40}\bminimum bid)\b",
            normalized,
        )
        unresolved_terminal_state = re.search(
            r"\bremains?\s+(?:out of|below)\b.{0,50}"
            r"\b(?:compliance|minimum bid|listing requirement)\b",
            normalized,
        )
        achieved_compliance_matches = tuple(re.finditer(
            r"\b(?:(?:has|have|had)\s+(?:(?:now|since|successfully)\s+)?)?regained"
            r"(?:\s+(?:full|continued|exchange|listing|market|minimum|bid|minimum-bid|nasdaq|nyse)){0,5}"
            r"\s+compliance\b|"
            r"\bregains"
            r"(?:\s+(?:full|continued|exchange|listing|market|minimum|bid|minimum-bid|nasdaq|nyse)){0,5}"
            r"\s+compliance\b|"
            r"\btitle:\s*[^\n]{0,120}\bregaining of"
            r"(?:\s+(?:full|continued|exchange|listing|market|minimum|bid|minimum-bid|nasdaq|nyse)){0,5}"
            r"\s+compliance\b",
            normalized,
        ))
        achieved_compliance = (
            achieved_compliance_matches[-1]
            if achieved_compliance_matches
            else None
        )
        if later_adverse_state or unresolved_terminal_state:
            return "negative", 3
        if achieved_compliance and (
            unachieved_compliance is None
            or achieved_compliance.start() >= unachieved_compliance.end()
        ):
            return "positive", 2
        if unachieved_compliance:
            return "negative", 3
        if re.search(
            r"\b(?:delisting|non[- ]?compliance)\b|"
            r"\b(?:no longer|fail(?:s|ed)? to) meet(?:s)?\b.{0,50}\b(?:minimum bid|listing requirement)\b|"
            r"\bminimum bid\b.{0,80}\b(?:deficien(?:cy|t)|below|notice)\b|"
            r"\b(?:deficien(?:cy|t)|below)\b.{0,80}\bminimum bid\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:continued listing|listing extension|listing status)\b.{0,100}"
            r"\b(?:condition(?:ed|al)|subject to|requires?|must)\b.{0,180}"
            r"\b(?:reverse (?:stock |share )?split|share consolidation|share combination|"
            r"regain|restore|cure|remedy|minimum bid|listing compliance)\b|"
            r"\b(?:condition(?:ed|al)|subject to|requires?|must)\b.{0,180}"
            r"\b(?:regain|restore|cure|remedy|bring\b.{0,50}\binto)\b"
            r".{0,100}\b(?:compliance|minimum bid|listing requirement)\b",
            normalized,
        ):
            return "negative", 2
        if re.search(
            r"\b(?:did not|failed to)\b.{0,40}\b(?:receive|obtain)\b"
            r".{0,80}\blisting extension\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:reverse (?:stock |share )?split|share consolidation|share combination)\b"
            r".{0,220}\b(?:expect(?:s|ed)?|intend(?:s|ed)?|plans?|aims?|seeks?|designed|purpose|allow)\b"
            r".{0,180}\b(?:regain|comply|satisfy|meet)\b.{0,100}"
            r"\b(?:compliance|minimum bid|listing requirement|continued listing)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:gets?|got|receiv(?:e[sd]?|ing)|grant(?:s|ed|ing))\b"
            r".{0,100}\blisting extension\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:grace|compliance|cure) period\b.{0,120}"
            r"\b(?:regain|minimum bid|listing compliance)\b|"
            r"\b\d+[- ]day period\b.{0,120}\b(?:regain|minimum bid)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:gets?|got|receiv(?:e[sd]?|ing)|receipt|grant(?:s|ed|ing)|extension)\b"
            r".{0,140}\b(?:extension|additional \d+ (?:calendar )?days?|"
            r"additional \d+ (?:calendar )?day period)\b"
            r".{0,140}\b(?:regain compliance|minimum bid)",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\bhas\s+(?:until|til|till)\b.{0,120}\bto\s+regain\b.{0,80}"
            r"\b(?:compliance|minimum bid|listing requirement)\b|"
            r"\bplans?\s+to\s+file\b.{0,160}\bto\s+regain\b.{0,80}"
            r"\b(?:compliance|listing requirement)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:period|deadline|extension|plans?|measures|opportunity|time|until)\b"
            r".{0,160}\b(?:to\s+)?regain\b.{0,80}"
            r"\b(?:compliance|minimum bid|listing requirement)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\bnot\b.{0,30}\b(?:intended|designed|expected)\b.{0,120}"
            r"\b(?:regain|restore|comply|cure|remedy|bring\b.{0,50}\binto)\b"
            r".{0,100}\b(?:compliance|minimum bid|listing requirement)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:work(?:s|ed|ing)? to|seeks? to|aims? to|expect(?:s|ed)? to|intend(?:s|ed)? to|"
            r"designed to|in order to)\b.{0,100}"
            r"\b(?:regain|restore|cure|remedy|bring\b.{0,50}\binto)\b"
            r".{0,100}\b(?:compliance|minimum bid|listing requirement)\b|"
            r"\b(?:reverse (?:stock |share )?split|share consolidation|share combination)\b"
            r".{0,140}\b(?:to|in order to)\s+"
            r"(?:regain|restore|cure|remedy)\b.{0,100}"
            r"\b(?:compliance|minimum bid|listing requirement)\b|"
            r"\b(?:reverse (?:stock |share )?split|share consolidation|share combination)\b"
            r".{0,180}\b(?:seeks? to|aims? to|expect(?:s|ed)? to|intend(?:s|ed)? to|"
            r"designed to|in order to)\b.{0,100}\bcomply\b.{0,100}\bminimum bid\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:expect(?:s|ed)?|intend(?:s|ed)?|plans?|aims?|seeks?)\b"
            r".{0,220}(?:"
            r"\b(?:allow\b.{0,100})?regain\b.{0,100}\b(?:compliance|listing requirement)\b|"
            r"\b(?:comply|satisfy|meet)\b.{0,100}\bminimum bid\b)",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\bto regain\b.{0,50}\bcompliance\b.{0,160}"
            r"\b(?:must|required|needs? to)\b",
            normalized,
        ):
            return "negative", 3
    if rule.concept == "commercial.contract":
        if re.search(
            r"\b(?:waiting|review|statutory|regulatory) period\b.{0,100}"
            r"\bextend(?:s|ed|ing)?\b.{0,80}\b(?:by )?agreement\b|"
            r"\btolling agreement\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:contract|agreement)\s+(?:termination|cancellation|non[- ]renewal)\b|"
            r"\b(?:termination|cancellation|non[- ]renewal)\b.{0,80}\b(?:contract|agreement)\b|"
            r"\b(?:contract|agreement)\b.{0,80}\b(?:terminated|cancelled|canceled|not renewed)\b",
            normalized,
        ):
            return "negative", 4
        if re.search(r"\bif\b.{0,120}\b(?:terminat\w*|cancel\w*)\b.{0,120}\b(?:enter|agreement)\b", normalized):
            return "neutral", 0
        if re.search(
            r"\b(?:previously|historically|milestones? achieved|over the past|in (?:19|20)\d{2})\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\bwaiv(?:e|es|ed|ing)\b.{0,120}\bright to terminate\b"
            r".{0,120}\b(?:fund|financ|tranche|obligation)\w*\b",
            normalized,
        ):
            return "positive", 2
        if re.search(r"\b(?:agreed to|accept(?:s|ed)?)\b.{0,80}\bfurther restrictions\b", normalized):
            return "negative", 2
        if re.search(
            r"\bsettlement agreement\b.{0,180}\b(?:cash payment|warrant issuance|pay(?:s|ment)?)\b",
            normalized,
        ):
            return "negative", 2
        if re.search(r"\bsecures?\b.{0,40}\b(?:securities purchase|account control|credit|loan) agreement\b", normalized):
            return "neutral", 0
        if re.search(r"\b(?:announc\w*|receiv\w*|win\w*|award\w*)\b.{0,80}\bcontract award\b|\bcontract award\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:awarded|wins?|receives?|secures?|sign(?:s|ed|ing)?|enters?)\b.{0,120}\b(?:contract|agreement|award|order)\b", normalized):
            return "positive", 2
        if any(
            re.search(
                r"\b(?:renew(?:s|ed|ing)?|extend(?:s|ed|ing)?|expand(?:s|ed|ing)?)\b"
                r".{0,100}\bagreements?\b.{0,40}\bwith\s*$",
                text[max(0, start - 180):start],
                re.I,
            )
            for start, _end in _entity_mention_spans(entity, text, mention_terms)
        ):
            return "neutral", 0
        if re.search(
            r"\bselected\b.{0,120}\bto (?:provide|deliver|supply)\b|"
            r"\b(?:expand(?:s|ed|ing)?|extend(?:s|ed|ing)?|renew(?:s|ed|ing)?)\b"
            r".{0,100}\b(?:sales |supply |service )?agreements?\b",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:enter(?:s|ed)? into)\b.{0,100}\bagreements?\b|"
            r"\bdeal with\b.{0,100}\b(?:provid|supply)\w*\b|"
            r"\bnamed\b.{0,100}\bofficial\b.{0,60}\b(?:broker|provider|supplier|partner)\b",
            normalized,
        ):
            return "positive", 2
    if rule.concept == "commercial.partnership":
        if re.search(
            r"\b(?:terminat\w*|cancel\w*|non[- ]renew\w*|end(?:s|ed|ing)?)\b.{0,100}\b(?:partnership|collaboration|alliance|joint venture)\b|"
            r"\b(?:partnership|collaboration|alliance|joint venture)\b.{0,100}\b(?:terminat\w*|cancel\w*|non[- ]renew\w*|end(?:s|ed|ing)?)\b|"
            r"\b(?:surprised|disappointed)\b.{0,120}\b(?:partnership|collaboration|alliance|joint venture)\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\b(?:has|have|had)?\s*expand(?:s|ed|ing)?\b.{0,100}"
            r"\bpreviously[- ]announced\b.{0,100}\b(?:partnership|collaboration|alliance|joint venture)\b",
            normalized,
        ):
            return "positive", 2
        if re.search(r"\bpreviously\b|\bhistorically\b", normalized) or re.search(
            r"\b(?:announced|began|entered|established|formed|launched|signed)\b"
            r".{0,120}\b(?:partnership|collaboration|alliance|joint venture)\b"
            r".{0,80}\bin (?:19|20)\d{2}\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:announc\w*|begin\w*|enter\w*|establish\w*|expand\w*|extend\w*|form\w*|launch\w*|renew\w*|sign\w*)\b.{0,100}\b(?:partnership|collaboration|alliance|joint venture)\b|"
            r"\b(?:partnership|collaboration|alliance|joint venture)\b.{0,100}\b(?:announc\w*|begin\w*|enter\w*|establish\w*|expand\w*|extend\w*|form\w*|launch\w*|renew\w*|sign\w*)\b|"
            r"\bpartner(?:s|ed|ing)? with\b|\bnamed\b.{0,80}\b(?:exclusive|official)\b.{0,60}\bpartner\b",
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
        if re.search(r"\b(?:excluding?|exclude[sd]?)\b.{0,80}\brestructuring\b|\bcontract restructuring\b", normalized):
            return "neutral", 0
        if re.search(
            r"\b(?:no|without)\s+(?:voluntary\s+)?workforce reduction\b|"
            r"\bretain\w*\b.{0,100}\b(?:management|employees?|workforce)\b",
            normalized,
        ):
            return "neutral", 0
        if _distress_subject_is_other_entity(entity, text, mention_terms):
            return "neutral", 0
        if re.search(r"\bconcerns? over (?:the )?sustainability of (?:this|the) business model\b", normalized):
            return "negative", 2
        if (
            rule.concept == "operations.business_update"
            and re.search(
                r"\b(?:expan(?:d|ds|ded|ding|sion)|opens?)\b.{0,100}"
                r"\b(?:clinics?|locations?|networks?|stores?|facilit(?:y|ies)|offices?|dispensar(?:y|ies))\b",
                normalized,
            )
        ):
            return (
                ("positive", 1)
                if re.search(r"\b(?:consider(?:s|ed|ing)?|plans?|intends?|expects?|to expand)\b", normalized)
                else ("positive", 2)
            )
        if re.search(r"\b(?:layoffs?|laid off|job cuts?|workforce reduction|restructur\w*)\b", normalized):
            return "negative", 3
        if re.search(r"\b(?:downside risk|growth pressure|margin pressure)\b", normalized):
            return "negative", 3
        if rule.concept == "operations.business_update" and re.search(
                r"\b(?:service outage|experiencing outages?|problems accessing|"
            r"restricted from (?:unloading|shipping|selling)|export restrictions?|"
            r"block(?:s|ed|ing)?\b.{0,50}\bexports?)\b",
            normalized,
        ):
            return "negative", 2
        if rule.concept == "operations.business_update" and re.search(
            r"\b(?:demand softness|lukewarm demand|cut(?:s|ting)?\s+\d[\d,]*.{0,50}\b(?:jobs?|positions?))\b",
            normalized,
        ):
            return "negative", 3
        if (
            rule.concept == "operations.business_update"
            and re.search(
                r"\b(?:launch(?:es|ed)?|introduc(?:e|es|ed)|"
                r"grant(?:s|ed)?\b.{0,80}\b(?:patent|authorization)|"
                r"named\b.{0,80}\b(?:exclusive|official)\b|"
                r"biggest\b.{0,160}\bprogram|focus(?:es|ed|ing)? more on)\b",
                normalized,
            )
            and not re.search(
                r"\b(?:chief executive|chief financial|CEO|CFO|president|director|officer)\b",
                text,
                re.I,
            )
        ):
            return "positive", 1
    if rule.concept == "financial.loss_exposure":
        return "negative", 3
    if rule.concept == "capital.return":
        if re.search(
            r"\bsynthetic share repurchase\b|"
            r"\breturn\w*\b.{0,80}\bcapital\b.{0,80}\bshareholders?\b|"
            r"\bcapital repayment\b",
            normalized,
        ):
            return "positive", 2
        if re.search(r"\b(?:paus(?:e[sd]?|ing)|suspend(?:s|ed|ing)?|halt(?:s|ed|ing)?)\b.{0,80}\b(?:buyback|repurchase)\b", normalized):
            return "negative", 2
        if re.search(r"\b(?:authoriz(?:e[sd]?|ing)|approv(?:e[sd]?|ing)|enter(?:s|ed|ing)?|restart(?:s|ed|ing)?|commenc(?:e[sd]?|ing))\b.{0,100}\b(?:buyback|repurchase)\b|\b(?:buyback|repurchase)\b.{0,100}\b(?:authoriz(?:e[sd]?|ing)|approv(?:e[sd]?|ing))\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:extend|extension|additional .{0,20}period)\w*\b.{0,100}\b(?:buyback|repurchase)\b|\b(?:buyback|repurchase)\b.{0,100}\b(?:extend|extension)\w*\b", normalized):
            return "positive", 2
    if rule.concept == "ownership.position_change" and re.search(
        r"\bnew\s+\d+(?:\.\d+)?%?\s+stake\b", normalized
    ):
        return "positive", 2
    if rule.concept == "ownership.position_change" and re.search(
        r"\b(?:insider|president|director|officer|executive)\b.{0,100}"
        r"\b(?:bought|purchased)\b.{0,80}\bshares?\b",
        normalized,
    ):
        return "positive", 2
    if rule.concept == "ownership.position_change" and re.search(
        r"\b(?:insider|president|director|officer|executive)\b.{0,100}"
        r"\bsold\b.{0,80}\bshares?\b",
        normalized,
    ):
        return "negative", 2
    if rule.concept == "ownership.position_change" and re.search(
        r"\b(?:hopefully|if|would|could|may|might)\b.{0,160}"
        r"\b(?:stake|position|shares?|ownership)\b.{0,80}\b(?:acquir|buy|sell)\w*\b|"
        r"\b(?:stake|position|shares?|ownership)\b.{0,80}\b(?:acquir|buy|sell)\w*\b"
        r".{0,160}\b(?:hopefully|would|could|may|might)\b",
        normalized,
    ):
        return "neutral", 0
    if rule.concept == "credit.solvency" and re.search(
        r"\b(?:joint )?(?:plan of reorganization|reorganization plan)\b.{0,100}\b(?:approved|confirmed|stands approved)\b|"
        r"\b(?:approved|confirmed|stands approved)\b.{0,100}\b(?:joint )?(?:plan of reorganization|reorganization plan)\b",
        normalized,
    ):
        if _distress_subject_is_other_entity(entity, text, mention_terms):
            return "neutral", 0
        return "positive", 3
    if rule.concept == "credit.solvency" and re.search(
        r"\b(?:going concern|substantial doubt|creditor protection|financing shortfall)\b",
        normalized,
    ):
        return "negative", 3
    if rule.concept == "credit.solvency" and re.search(r"\b(?:chapter 11|bankrupt(?:cy)?)\b", normalized):
        if _distress_subject_is_other_entity(entity, text, mention_terms):
            return "neutral", 0
        if re.search(r"\b(?:successfully )?(?:complet(?:e[sd]?|ing)\b.{0,60})?(?:exit|emerg(?:e|es|ed|ing)|plan (?:approved|confirmed))\b.{0,100}\b(?:chapter 11|bankrupt)|\b(?:chapter 11|bankrupt)\b.{0,100}\b(?:exit|emerg(?:e|es|ed|ing)|plan (?:approved|confirmed)|reorganization (?:was )?(?:successfully )?completed)\b|\bsuccessfully completed\b.{0,80}\bchapter 11 reorganization\b", normalized):
            return "positive", 3
        if re.search(
            r"\bbankruptcy court\b.{0,100}\b(?:approval|approved|order|hearing|filing)\b|"
            r"\bunder (?:[a-z]+ )?bankruptcy law\b",
            normalized,
        ) and not re.search(
            r"\b(?:file[sd]?|filing|initiated|commenced|entered|petitioned|sought)\b"
            r".{0,80}\b(?:chapter 11|bankrupt(?:cy)?|insolvency)\b",
            normalized,
        ):
            return "neutral", 0
        if _entity_is_non_debtor_in_bankruptcy_context(entity, text, mention_terms):
            return "neutral", 0
        return "negative", 3
    if rule.concept == "legal.proceeding":
        if re.search(
            r"\bdismiss(?:es|ed) as moot\b.{0,120}\b(?:action|case|claim)\b|"
            r"\b(?:action|case|claim)\b.{0,120}\bdismiss(?:es|ed) as moot\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:wins?|won)\b.{0,100}\b(?:ip rights|patent|legal)\b"
            r".{0,80}\bcase\b",
            normalized,
        ):
            return "positive", 2
        if re.search(
            r"\b(?:sues?|sued)\b.{0,160}\b(?:discrimination|fraud|corruption|"
            r"violations?|claims?)\b|"
            r"\b(?:prosecution office|prosecutor|regulator|sec|department of labor)\b"
            r".{0,120}\binvestigates?\b|"
            r"\b(?:sec|regulator|authority)\b.{0,100}\bcharged\b.{0,140}"
            r"\b(?:fraud|corruption|violation)\b|"
            r"\bremand(?:s|ed)?\b.{0,100}\bfor trial\b|"
            r"\bintends? to defend\b.{0,80}\bclaims?\b",
            normalized,
        ):
            return "negative", 3
        if re.search(
            r"\bconfirms?\b.{0,80}\bsettlement agreement\b.{0,80}\beffective\b"
            r".{0,120}\b(?:following|after)\b.{0,80}\b(?:emergence|exit)\b"
            r".{0,50}\b(?:chapter 11|bankruptcy)\b",
            normalized,
        ):
            return "neutral", 0
        if re.search(
            r"\b(?:judge|court)\b.{0,180}\b(?:restriction|limitation|ban|rule|regulation)s?\b"
            r".{0,100}\b(?:arbitrary|unlawful|invalid|struck down)\b|"
            r"\b(?:judge|court)\b.{0,180}\b(?:strikes? down|invalidates?)\b"
            r".{0,100}\b(?:restriction|limitation|ban|rule|regulation)s?\b",
            normalized,
        ):
            return "positive", 1
        if re.search(
            r"\b(?:targeted by (?:the )?probe|under investigation|antitrust probe|"
            r"criminal (?:antitrust )?investigation|receiv(?:e[sd]?) (?:a )?subpoena)\b",
            normalized,
        ):
            return "negative", 2
        filing = re.search(
            r"\b(?:files?|filed|brings?|brought|initiates?|initiated)\b.{0,80}\b(?:lawsuit|complaint|action)\b.{0,100}\bagainst\b",
            normalized,
        )
        if filing and entity is not None:
            plaintiff_clause = text[:filing.start()] + text[filing.start():filing.end()].rsplit("against", 1)[0]
            return (
                ("neutral", 0)
                if _entity_in_quote(entity, plaintiff_clause, mention_terms)
                else ("negative", 2)
            )
        if entity is not None and re.search(
            r"\b(?:lawsuit alleges|aggressively challenging|seeking (?:both )?(?:monetary )?damages)\b",
            normalized,
        ):
            cue = re.search(r"\b(?:lawsuit alleges|aggressively challenging|seeking)\b", normalized)
            if cue and _entity_in_quote(entity, text[:cue.start()], mention_terms):
                return "neutral", 0
        if re.search(r"\b(?:grant(?:s|ed)?|receiv(?:e|es|ed))\b.{0,80}\bpatent\b", normalized):
            return "positive", 2
        if re.search(r"\bsettlement\b.{0,220}\b(?:cash payment|warrant issuance|pay(?:s|ment)?)\b", normalized):
            return "negative", 2
        settlement = re.search(r"\bsettlement agreement\b", normalized)
        if entity is not None and settlement is not None:
            with_cue = re.search(r"\bwith\b", normalized[settlement.end():])
            if with_cue is not None:
                counterparty_start = settlement.end() + with_cue.end()
                if _entity_in_quote(
                    entity,
                    text[counterparty_start:counterparty_start + 100],
                    mention_terms,
                ):
                    return "neutral", 0
        if re.search(r"\b(?:reaches?|announces?|entered into|approved)\b.{0,100}\bsettlement\b|\bsettlement agreement\b", normalized):
            return "positive", 2
    if rule.concept == "governance.management_change" and re.search(
        r"\b(?:death of|dies|died|passing of|passed away)\b.{0,100}"
        r"\b(?:chief executive|chief financial|CEO|CFO|president|founder|director)\b|"
        r"\b(?:chief executive|chief financial|CEO|CFO|president|founder|director)\b"
        r".{0,100}\b(?:dies|died|death|passing|passed away)\b",
        normalized,
    ):
        return "negative", 3
    if rule.concept == "governance.management_change" and re.search(
        r"\b(?:resigns?|retires?|steps down)\b.{0,140}\b(?:interim|successor|appoints?|named)\b|"
        r"\b(?:interim|successor|appoints?|named)\b.{0,140}\b(?:resigns?|retires?|steps down)\b",
        normalized,
    ):
        return "neutral", 0
    if rule.concept == "governance.management_change" and re.search(
        r"\b(?:happy|pleased|welcomes?|great step|positive step|right direction)\b"
        r".{0,180}\b(?:appoint(?:s|ed|ment)?|nam(?:e|ed)|elect(?:s|ed|ion)?)\b|"
        r"\b(?:appoint(?:s|ed|ment)?|nam(?:e|ed)|elect(?:s|ed|ion)?)\b"
        r".{0,180}\b(?:happy|pleased|welcomes?|great step|positive step|right direction)\b",
        normalized,
    ):
        return "positive", 2
    if (
        rule.concept == "governance.auditor_change"
        and re.search(r"\bat the request of\b.{0,100}\b", normalized)
        and re.search(r"\b(?:auditor|accounting firm)\b.{0,100}\bresign(?:s|ed|ation)?\b", normalized)
        and re.search(r"\b(?:appoint(?:s|ed)|names?|named)\b.{0,120}\b(?:successor|new) auditor\b", normalized)
    ) and not re.search(
        r"\b(?:disagreement|dispute|dismiss(?:ed|al)?|qualified opinion|adverse finding)\b",
        normalized,
    ):
        return "neutral", 0
    if rule.concept == "corporate_transaction.acquisition":
        if (
            role == "target"
            and entity is not None
            and re.search(r"\bremains? committed\b.{0,100}\boffer for\b", normalized)
        ):
            committed_target = re.split(
                r"\bremains? committed\b.{0,100}\boffer for\b",
                normalized,
                maxsplit=1,
            )[-1]
            if _entity_in_quote(entity, committed_target, mention_terms):
                return "positive", 3
        if re.search(
            r"\b(?:reject\w*|not in (?:the )?best interest|declines? (?:the )?(?:bid|offer))\b",
            normalized,
        ):
            return "negative", 3
        if role == "target": return "positive", 3
        if role == "acquirer" and re.search(
            r"\b(?:raises?|increases?|boosts?|sweetens?)\b.{0,80}\b(?:takeover )?(?:bid|offer)\b",
            normalized,
        ):
            return "negative", 2
        if re.search(r"\b(?:dilutive|difficult|struggle|overpay|debt burden|reject\w*|not in (?:the )?best interest|no longer pursue|abandon\w*|terminat\w*)\b", normalized): return "negative", 3
        if re.search(r"\b(?:regulator\w*|fdic|ftc|federal deposit insurance corporation)\b.{0,120}\bapprov(?:e|es|ed|al)\b.{0,100}\b(?:acquisition|merger|purchase)\b", normalized):
            return "positive", 2
        if re.search(r"\ball required regulatory approvals?\b.{0,60}\b(?:received|obtained)\b", normalized):
            return "positive", 2
        if re.search(r"\b(?:we|the company|[a-z0-9&.' -]+)\s+(?:have|has|had)\s+(?:invested\b.{0,100}\bto acquire|acquired)\b", normalized):
            return "positive", 2
        if role == "acquirer" and re.search(
            r"\b(?:agrees? to|will|to) (?:acquire|buy|purchase)\b|"
            r"\b(?:has|have|had) acquired\b|"
            r"\bannounc(?:e|es|ed|ing)\b.{0,80}\bacquisitions?\b"
            r".{0,120}\b(?:executed|completed|closed|closing|resulting in|"
            r"accretive|strategic|expand(?:s|ed|ing)?)\b|"
            r"\b(?:closes?|completes?|approved?)\b.{0,80}\b(?:acquisition|purchase|merger)\b|"
            r"\bpurchase of\b.{0,100}\b(?:assets?|business|operations?)\b",
            normalized,
        ):
            return "positive", 2
        if re.search(r"\b(?:accretive|synerg|complementary|strategic fit|fund(?:s|ed|ing)? (?:a |an |the |its )?(?:pending )?acquisition|increase\w* (?:the )?(?:company'?s )?(?:operational )?scale|expand\w* (?:the )?(?:company'?s )?(?:scale|footprint)|high[- ]value (?:development )?inventory)\w*\b", normalized): return "positive", 2
        if re.search(r"\b(?:will combine|amalgamat\w*|complet(?:e|es|ed|ion) of the (?:merger|combination))\b", normalized): return "positive", 2
        return "neutral", 0
    if rule.concept == "product.milestone" and re.search(
        r"\b(?:could|may|might|eventual|potential|even with)\b.{0,100}\b(?:approval|clearance|authorization)\b|"
        r"\b(?:approval|clearance|authorization)\b.{0,100}\b(?:could|may|might|eventual|potential)\b",
        normalized,
    ):
        return "neutral", 0
    if rule.concept == "governance.shareholder_vote":
        if re.search(r"\breject\w*\b.{0,80}\b(?:ban|restrict|audit)\b", normalized):
            return "positive", 2
        if re.search(r"\burges? shareholders? to vote for\b|\brecommends?\b.{0,80}\bvote for\b", normalized):
            return "positive", 1
        return "neutral", 0
    if rule.concept == "ownership.position" and re.search(
        r"\b(?:activist investor stake|activist (?:investor|campaign)|activist\b.{0,80}\btarget)\b",
        normalized,
    ):
        return "positive", 2
    if rule.concept == "financial.liquidity" and re.search(
        r"\b(?:Paycheck Protection Program|PPP) loan\b|"
        r"\bloan\b.{0,100}\bPaycheck Protection Program\b",
        normalized,
    ):
        return "positive", 1
    if rule.concept == "capital.deleveraging" and re.search(
        r"\b(?:proceeds?|funds?)\b.{0,120}\b(?:notes?|bonds?|debt)\s+offering\b.{0,160}"
        r"\b(?:repay|repayment|pay down)\b.{0,80}\b(?:revolver|credit facility|debt|borrowings?)\b|"
        r"\b(?:notes?|bonds?|debt)\s+offering\b.{0,160}\b(?:proceeds?|funds?)\b"
        r".{0,120}\b(?:repay|repayment|pay down)\b",
        normalized,
    ):
        return "neutral", 0
    if rule.concept == "strategy.operational_priority":
        if re.search(r"\bworking on\b.{0,100}\bprojects?\b|\bfocus(?:es|ed|ing)?(?: more)? on\b|\bbiggest\b.{0,160}\bprogram\b", normalized):
            return "positive", 1
    if rule.concept == "strategy.strategic_alternatives" and re.search(
        r"\b(?:continues?|advances?)\b.{0,80}\b(?:sale process|strategic alternatives?)\b|"
        r"\b(?:sale process|strategic alternatives?)\b.{0,80}\b(?:expedited|advances?|continues?)\b",
        normalized,
    ):
        return "positive", 1
    positive = _sentiment_cue_count(rule.positive, normalized)
    negative = _sentiment_cue_count(rule.negative, normalized)
    if positive > negative: return "positive", min(4, 1 + positive)
    if negative > positive: return "negative", min(4, 1 + negative)
    return "neutral", 0


def _positive_growth_fact(typed_facts: Sequence[Mapping[str, Any]]) -> bool:
    for fact in typed_facts:
        raw_value = (
            fact.get("lower_value")
            if fact.get("fact_type") == "percentage_range"
            else fact.get("value") if fact.get("fact_type") == "percentage" else None
        )
        try:
            if raw_value is not None and float(str(raw_value)) > 0:
                return True
        except ValueError:
            continue
    return False


def _material_cash_consideration(typed_facts: Sequence[Mapping[str, Any]]) -> bool:
    """Identify transaction cash amounts large enough to be decision-relevant."""
    scales = {
        "units": 1.0,
        "thousand": 1e3,
        "million": 1e6,
        "billion": 1e9,
        "trillion": 1e12,
    }
    for fact in typed_facts:
        if fact.get("fact_type") != "money":
            continue
        try:
            value = float(str(fact.get("value") or "0")) * scales.get(
                str(fact.get("magnitude") or "units"),
                1.0,
            )
        except ValueError:
            continue
        if value >= 100_000_000:
            return True
    return False


_REPORTED_PERIOD_COMPARISON_RE = re.compile(
    r"\b(?:adjusted\s+|diluted\s+)?(?P<metric>EPS|earnings per share|revenues?|sales|net income|profit|net loss|loss)\b"
    r".{0,60}?(?:E?\$|Â£|â‚¬)?\s*(?P<first>\(?-?\d[\d,]*(?:\.\d+)?\)?)(?P<first_unit>\s*(?:K|M|B|T|thousand|million|billion|trillion))?\s*"
    r"(?P<operator>vs\.?|versus|compared (?:with|to)|up from|down from)\s*"
    r"(?:E?\$|Â£|â‚¬)?\s*(?P<second>\(?-?\d[\d,]*(?:\.\d+)?\)?)(?P<second_unit>\s*(?:K|M|B|T|thousand|million|billion|trillion))?"
    r"(?P<context>.{0,50})",
    re.I,
)


def _reported_period_comparison_direction(normalized_text: str) -> str | None:
    """Compare realized metrics only when the comparator is a prior period."""
    state_comparison = re.search(
        r"\b(?:adj(?:usted)?\.?\s+)?(?:EBITDA|income|profit)\s+"
        r"(?P<first_state>loss|gain|profit)\b.{0,30}?"
        r"(?P<first>\d[\d,]*(?:\.\d+)?)\s*(?P<first_unit>[KMBT])?\s*"
        r"(?:vs\.?|versus|compared (?:with|to))\s*"
        r"(?P<second_state>loss|gain|profit)\b.{0,30}?"
        r"(?P<second>\d[\d,]*(?:\.\d+)?)\s*(?P<second_unit>[KMBT])?"
        r".{0,50}\b(?:yoy|year[- ]over[- ]year|last year|prior year|year ago)\b",
        normalized_text,
        re.I,
    )
    if state_comparison:
        def state_value(state: str, raw: str, unit: str | None) -> float:
            value = float(raw.replace(",", "")) * {
                "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
            }.get((unit or "").casefold(), 1.0)
            return -value if state.casefold() == "loss" else value

        first = state_value(
            state_comparison.group("first_state"),
            state_comparison.group("first"),
            state_comparison.group("first_unit"),
        )
        second = state_value(
            state_comparison.group("second_state"),
            state_comparison.group("second"),
            state_comparison.group("second_unit"),
        )
        return "positive" if first > second else "negative" if first < second else "neutral"
    # Quarter identifiers are horizons, not numeric metric values. Removing
    # them prevents text such as "revenue for Q1 up from $1.8M" from treating
    # the 1 in Q1 as the reported revenue.
    normalized_text = re.sub(r"\bq[1-4]\b", " ", normalized_text, flags=re.I)
    match = _REPORTED_PERIOD_COMPARISON_RE.search(normalized_text)
    if not match:
        return None
    operator = match.group("operator").casefold()
    if operator not in {"up from", "down from"} and not re.search(
        r"\b(?:yoy|year[- ]over[- ]year|same (?:quarter|qtr)|last year|prior year|year ago|in (?:19|20)\d{2})\b",
        match.group("context"),
        re.I,
    ):
        return None

    def scaled_value(raw: str, unit: str | None) -> float:
        negative = raw.startswith("-") or (raw.startswith("(") and raw.endswith(")"))
        value = float(raw.strip("()-").replace(",", ""))
        scale = {
            "k": 1e3,
            "thousand": 1e3,
            "m": 1e6,
            "million": 1e6,
            "b": 1e9,
            "billion": 1e9,
            "t": 1e12,
            "trillion": 1e12,
        }.get(
            (unit or "").strip().casefold(), 1.0
        )
        return (-value if negative else value) * scale

    first = scaled_value(match.group("first"), match.group("first_unit"))
    second = scaled_value(match.group("second"), match.group("second_unit"))
    if match.group("metric").casefold() in {"loss", "net loss"}:
        first, second = -abs(first), -abs(second)
    if first > second:
        return "positive"
    if first < second:
        return "negative"
    return "neutral"


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


def _entity_possessive_in_quote(
    entity: Mapping[str, Any],
    quote: str,
    mention_terms: Sequence[str] = (),
) -> bool:
    values = (*mention_terms, str(entity.get("display_name") or ""))
    for value in values:
        tokens = re.findall(r"[A-Za-z0-9]+", str(value))
        if not tokens or not _safe_alias(_normalize_alias(value)):
            continue
        name_pattern = r"[^A-Za-z0-9]+".join(re.escape(token) for token in tokens)
        if re.search(rf"(?<![A-Za-z0-9]){name_pattern}(?:'s|’s)(?![A-Za-z0-9])", quote, re.I):
            return True
    return False


def _document_ticker_aliases(text: str) -> dict[str, tuple[str, ...]]:
    """Derive article-local company aliases from explicit exchange/ticker anchors."""
    aliases: dict[str, list[str]] = {}
    pattern = re.compile(
        r"(?:^|[\n.!?])\s*(?:Title:\s*|Teaser:\s*)?"
        r"(?P<name>[^\n.!?()]{2,100}?)\s*"
        r"\((?:NASDAQ|NYSE|NYSE\s+AMERICAN|NYSEAMERICAN|AMEX|OTC(?:QX|QB)?|TSX|TSXV|CSE)"
        r"\s*[:\-]\s*(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\)",
        re.I,
    )
    for match in pattern.finditer(text):
        ticker = _normalize_ticker_identifier(match.group("ticker"))
        name = re.sub(r"^(?:title|teaser)\s*:\s*", "", match.group("name"), flags=re.I).strip(" ,-:")
        if not ticker or not _safe_alias(_normalize_alias(name)):
            continue
        aliases.setdefault(ticker, []).extend(_alias_variants(name))
    return {
        ticker: tuple(dict.fromkeys(values))
        for ticker, values in aliases.items()
    }


def _subsidiary_ipo_role(
    entity: Mapping[str, Any] | None,
    text: str,
    mention_terms: Sequence[str],
) -> str:
    """Classify an IPO participant as issuer, parent, or unrelated mention."""
    if entity is None:
        return "issuer"
    normalized = text.casefold()
    if not re.search(r"\b(?:initial public offering|ipo)\b", normalized):
        return ""
    parent_matches = list(re.finditer(
        r"\b(?:wholly[- ]owned|majority[- ]owned|controlled)?\s*subsidiary of\s+"
        r"(?P<parent>[A-Z][A-Za-z0-9&./'\-]{1,40})",
        text,
        re.I,
    ))
    ticker = _normalize_alias(str(entity.get("ticker") or "")).replace(" ", "")
    for match in parent_matches:
        parent = match.group("parent").strip(" ,.-")
        parent_key = _normalize_alias(parent).replace(" ", "")
        if _entity_in_quote(entity, parent, mention_terms) or (
            len(parent_key) >= 2
            and ticker.startswith(parent_key)
            and len(ticker) - len(parent_key) <= 1
        ):
            return "parent"
    if _entity_in_quote(entity, text, mention_terms):
        return "issuer"
    return "unrelated"


def _entities_for_quote(
    entities: Sequence[Mapping[str, Any]],
    quote: str,
    previous_entity_ids: Sequence[str],
    mention_terms: Mapping[str, Sequence[str]],
    *,
    inherit_subject: bool = False,
    require_grounded_entity_ids: set[str] | frozenset[str] = frozenset(),
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
    previous = set(previous_entity_ids)
    if len(entities) == 1:
        entity = entities[0]
        entity_id = str(entity.get("entity_id") or "")
        if entity_id in require_grounded_entity_ids and entity_id not in previous:
            return []
        return [entity]
    inherited = [row for row in entities if str(row["entity_id"]) in previous]
    if inherit_subject and inherited:
        return inherited
    return inherited if len(inherited) == 1 else []


def _apply_attributed_claim_sources(
    statements: Sequence[Mapping[str, Any]],
    participations: Sequence[Mapping[str, Any]],
    entities: Sequence[Mapping[str, Any]],
    mention_terms: Mapping[str, Sequence[str]],
    *,
    candidate_tickers: Sequence[str],
) -> list[dict[str, Any]]:
    """Keep a named external analyst source out of issuer sentiment.

    A listed research firm named before an attribution verb is not an affected
    issuer merely because its name identifies the source. The deterministic
    identity layer represents it as a security, while the contract reserves
    ``claim_source`` for organization/person entities, so this unrelated
    participation is omitted. Provider candidates remain eligible subjects.
    """
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    entity_by_id = {str(row["entity_id"]): row for row in entities}
    candidates = {
        _normalize_ticker_identifier(value) for value in candidate_tickers if value
    }
    result: list[dict[str, Any]] = []
    for participation in participations:
        row = dict(participation)
        statement = statement_by_id.get(str(row.get("statement_id") or ""), {})
        if not str(statement.get("concept_leaf") or "").startswith("analyst."):
            result.append(row)
            continue
        quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        attribution = re.search(
            r"\b(?:publishes?|writes?|tells?|discuss(?:es|ing)|defends?)\b|"
            r"\b(?:out\s+)?in\s+defen[cs]e\s+of\b",
            quote,
            re.I,
        )
        entity = entity_by_id.get(str(row.get("entity_id") or ""))
        ticker = _normalize_ticker_identifier(entity.get("ticker") if entity else "")
        if (
            attribution is not None
            and entity is not None
            and ticker not in candidates
            and _entity_in_quote(
                entity,
                quote[:attribution.start()],
                mention_terms.get(str(entity["entity_id"]), ()),
            )
        ):
            continue
        result.append(row)
    return result


def _regulatory_entities_for_facts(
    entities: Sequence[Mapping[str, Any]],
    quote: str,
    typed_facts: Sequence[Mapping[str, Any]],
    mention_terms: Mapping[str, Sequence[str]],
) -> list[Mapping[str, Any]]:
    """Bind a regulatory outcome to the closest explicitly named issuer."""
    if len(entities) <= 1:
        return list(entities)
    decisions = [
        fact
        for fact in typed_facts
        if fact.get("fact_type") == "regulatory_decision"
        and fact.get("outcome_class") in {"favorable", "adverse"}
    ]
    if not decisions:
        return list(entities)
    selected_ids: set[str] = set()
    licensee_cue = re.search(r"\b(?:its|the)\s+licensee\b", quote, re.I)
    if licensee_cue is not None and any(
        fact.get("outcome_class") == "adverse" for fact in decisions
    ):
        selected_ids.update(
            str(entity["entity_id"])
            for entity in entities
            if _entity_in_quote(
                entity,
                quote[:licensee_cue.start()],
                mention_terms.get(str(entity["entity_id"]), ()),
            )
        )
    for fact in decisions:
        subject = str(fact.get("subject_raw") or "")
        subject_entities = [
            entity
            for entity in entities
            if subject and _entity_in_quote(
                entity,
                subject,
                mention_terms.get(str(entity["entity_id"]), ()),
            )
        ]
        if subject_entities:
            selected_ids.update(str(entity["entity_id"]) for entity in subject_entities)
            continue
        cue_start = int(fact.get("start") or 0)
        cue_end = int(fact.get("end") or cue_start)
        distances: list[tuple[int, str]] = []
        for entity in entities:
            entity_id = str(entity["entity_id"])
            spans = _entity_mention_spans(
                entity,
                quote,
                mention_terms.get(entity_id, ()),
            )
            if spans:
                distance = min(
                    max(cue_start - end, start - cue_end, 0)
                    for start, end in spans
                )
                distances.append((distance, entity_id))
        if distances:
            nearest = min(distance for distance, _entity_id in distances)
            selected_ids.update(
                entity_id for distance, entity_id in distances if distance == nearest
            )
    selected = [entity for entity in entities if str(entity["entity_id"]) in selected_ids]
    return selected or list(entities)


def _guidance_entities_for_quote(
    entities: Sequence[Mapping[str, Any]],
    quote: str,
    mention_terms: Mapping[str, Sequence[str]],
    *,
    inherited_entity_ids: Sequence[str] = (),
    allow_unmentioned: bool = True,
) -> list[Mapping[str, Any]]:
    """Bind issuer guidance to the named subject, not a later counterparty."""
    action = re.search(
        r"\b(?:rais(?:e[sd]?|ing)|boost(?:s|ed|ing)?|increas(?:e[sd]?|ing)|"
        r"lower(?:s|ed|ing)?|cut(?:s|ting)?|reaffirm(?:s|ed|ing)?|"
        r"withdraw(?:s|n|ing)?|project(?:s|ed|ing)?|expect(?:s|ed|ing)?|"
        r"anticipat(?:e[sd]?|ing)|sees?)\b",
        quote,
        re.I,
    )
    if action is None or len(entities) <= 1:
        if action is None:
            return list(entities)
        mentioned = [
            entity
            for entity in entities
            if _entity_mention_spans(
                entity,
                quote,
                mention_terms.get(str(entity["entity_id"]), ()),
            )
        ]
        if len(entities) == 1 and mentioned and all(
            start >= action.end()
            for start, _end in _entity_mention_spans(
                entities[0],
                quote,
                mention_terms.get(str(entities[0]["entity_id"]), ()),
            )
        ) and not re.search(
            r"\b(?:guidance|outlook|forecast)\b.{0,40}\b(?:from|by)\b",
            quote,
            re.I,
        ):
            return []
        return list(entities)
    preceding: list[tuple[int, Mapping[str, Any]]] = []
    for entity in entities:
        spans = _entity_mention_spans(
            entity,
            quote,
            mention_terms.get(str(entity["entity_id"]), ()),
        )
        distances = [action.start() - end for _start, end in spans if end <= action.start()]
        if distances:
            preceding.append((min(distances), entity))
    if preceding:
        nearest = min(distance for distance, _entity in preceding)
        return [entity for distance, entity in preceding if distance == nearest]
    mentioned = any(
        _entity_mention_spans(
            entity,
            quote,
            mention_terms.get(str(entity["entity_id"]), ()),
        )
        for entity in entities
    )
    if mentioned:
        return []
    inherited = set(inherited_entity_ids)
    if inherited:
        return [
            entity for entity in entities
            if str(entity.get("entity_id") or "") in inherited
        ]
    return list(entities) if allow_unmentioned else []


def _relationship_scoped_regulatory_entities(
    entities: Sequence[Mapping[str, Any]],
    scoped_entities: Sequence[Mapping[str, Any]],
    quote: str,
    mention_terms: Mapping[str, Sequence[str]],
    regulatory_facts: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Attach a named approval to one explicit provider-scoped economic partner.

    Headlines sometimes lead with the focal issuer but omit its exchange ticker:
    ``Issuer partner Counterparty announces FDA approval``.  The relationship
    makes the leading issuer an affected product partner, but it does not justify
    propagating the event to arbitrary co-mentioned or ambiguous securities.
    """
    favorable = any(
        fact.get("outcome_class") == "favorable" for fact in regulatory_facts
    ) or bool(re.search(
        r"\b(?:FDA|EMA|regulatory)\b.{0,60}\b(?:approval|authorization|clearance)\b",
        quote,
        re.I,
    ))
    if not favorable:
        return list(scoped_entities)
    provider_only = [
        entity
        for entity in entities
        if "provider_candidate_only" in entity.get("identity_evidence", ())
        and entity not in scoped_entities
    ]
    if len(provider_only) != 1 or not scoped_entities:
        return list(scoped_entities)
    relation = re.match(
        r"\s*(?:title\s*:\s*)?[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,5}"
        r"\s+(?:partner|licensee)\s+",
        quote,
        re.I,
    )
    action = re.search(
        r"\b(?:announc(?:e[sd]?|ing)|receiv(?:e[sd]?|ing)|secur(?:e[sd]?|ing))\b"
        r".{0,100}\b(?:FDA|EMA|regulatory)\b.{0,60}\b(?:approval|authorization|clearance)\b|"
        r"\b(?:FDA|EMA|regulatory)\b.{0,60}\b(?:approval|authorization|clearance)\b",
        quote,
        re.I,
    )
    if relation is None or action is None or relation.end() >= action.start():
        return list(scoped_entities)
    counterparty_segment = quote[relation.end():action.start()]
    if not any(
        _entity_in_quote(
            entity,
            counterparty_segment,
            mention_terms.get(str(entity["entity_id"]), ()),
        )
        for entity in scoped_entities
    ):
        return list(scoped_entities)
    return [*scoped_entities, provider_only[0]]


def _entity_mention_spans(
    entity: Mapping[str, Any],
    quote: str,
    mention_terms: Sequence[str],
) -> list[tuple[int, int]]:
    values = (*mention_terms, str(entity.get("display_name") or ""), str(entity.get("ticker") or ""))
    patterns: set[str] = set()
    for value in values:
        tokens = re.findall(r"[A-Za-z0-9]+", str(value))
        if not tokens:
            continue
        normalized = " ".join(token.casefold() for token in tokens)
        ticker = _normalize_alias(entity.get("ticker") or "")
        if not (_safe_alias(normalized) or normalized == ticker):
            continue
        patterns.add(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]+".join(map(re.escape, tokens)) + r"(?![A-Za-z0-9])")
    return [
        match.span()
        for pattern in patterns
        for match in re.finditer(pattern, quote, re.I)
    ]


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


_ISSUER_EVENT_ASSERTION_RE = re.compile(
    r"\b(?:announc\w*|report\w*|say(?:s|ing)?|said|see(?:s|ing)?|expect\w*|"
    r"grant\w*|approv\w*|authoriz\w*|launch\w*|introduc\w*|deliver\w*|obtain\w*|"
    r"open\w*|extend\w*|renew\w*|publish\w*|resume\w*|consolidat\w*|extinguish\w*|"
    r"secur\w*|enter\w*|expand\w*|partner\w*|acquir\w*|buy(?:s|ing)?|"
    r"sell\w*|reject\w*|offer\w*|file\w*|rais\w*|cut\w*|"
    r"increase\w*|decrease\w*|declin\w*|gain\w*|loss(?:es)?|miss\w*|"
    r"target\w*|activist\b|block\w*|ban\w*|restrict\w*|export\w*|"
    r"beat\w*|inline|up\s+\d|down\s+\d|outages?|layoffs?|job cuts?|"
    r"tender offer|takeover|merger|bid\b|stake\b|patent\b|study results?|"
    r"clinical data|guidance\b|outlook\b|EPS\b|revenues?\b|sales\b|loan\b|"
    r"(?:soft\w*|weak\w*|lukewarm|strong|rising|declining|slowing|consumer)\s+demand|"
    r"demand\s+(?:softness|weakness|strength|growth|decline)|"
    r"price target|rating\b|delisting|non[- ]compliance|special meeting|"
    r"commercializ\w*|will not be available|unavailable\b|compelling\b|record\b|approval\b|authorization\b|FDA nod\b)\b",
    re.I,
)


def _has_issuer_event_assertion(text: str) -> bool:
    """Recognize event-bearing issuer text independently of concept coverage."""
    if re.search(
        r"\b(?:inflation|consumer price index|producer price index|unemployment|"
        r"nonfarm payrolls?|jobless claims|gross domestic product)\b",
        text,
        re.I,
    ) and not re.search(
        r"\b(?:company|corporation|inc\.?|ltd\.?|plc|shares?|stock|issuer|management|board)\b",
        text,
        re.I,
    ):
        return False
    return bool(_ISSUER_EVENT_ASSERTION_RE.search(text))


_STRICT_LOCAL_EVENT_PREDICATE_RE = re.compile(
    r"\b(?:announc(?:e[sd]?|ing)|expect(?:s|ed|ing)?|introduc(?:e[sd]?|ing)|"
    r"obtain(?:s|ed|ing)?|resum(?:e[sd]?|ing)|start(?:s|ed|ing)?|stop(?:s|ped|ping)?|"
    r"open(?:s|ed|ing)?|clos(?:e[sd]?|ing)|launch(?:e[sd]?|ing)|"
    r"ship(?:s|ped|ping)?|extend(?:s|ed|ing)?|renew(?:s|ed|ing)?|"
    r"merg(?:e[sd]?|ing)|consolidat(?:e[sd]?|ing)|award(?:s|ed|ing)?|"
    r"grant(?:s|ed|ing)?|fil(?:e[sd]?|ing)|publish(?:es|ed|ing)?|"
    r"acquir(?:e[sd]?|ing)|sell(?:s|ing)?|sold|invest(?:s|ed|ing)?|"
    r"explor(?:e[sd]?|ing)|increas(?:e[sd]?|ing)|reduc(?:e[sd]?|ing)|"
    r"extinguish(?:es|ed|ing)?|report(?:s|ed|ing)?|alleg(?:e[sd]?|ing)|"
    r"downgrad(?:e[sd]?|ing)|affirm(?:s|ed|ing)?|dismiss(?:es|ed|ing)?)\b",
    re.I,
)


def _entity_is_local_event_subject(
    entity: Mapping[str, Any],
    quote: str,
    mention_terms: Sequence[str],
) -> bool:
    """Require a resolved, same-span entity to govern the fallback predicate."""
    if (
        not _has_issuer_event_assertion(quote)
        or _boilerplate_sentence(quote)
        or _forward_looking_boilerplate_sentence(quote)
        or re.search(r"\bprice action\b|\bshares? (?:rose|fell|gained|lost|traded)\b", quote, re.I)
    ):
        return False
    predicates = sorted(
        (
            *list(_STRICT_LOCAL_EVENT_PREDICATE_RE.finditer(quote)),
            *list(_ISSUER_EVENT_ASSERTION_RE.finditer(quote)),
        ),
        key=lambda match: (match.start(), match.end()),
    )
    if not predicates:
        return False
    for start, end in _entity_mention_spans(entity, quote, mention_terms):
        if any(0 <= predicate.start() - end <= 120 for predicate in predicates):
            return True
        if re.search(
            r"\b(?:award(?:s|ed)?|grant(?:s|ed)?|dismiss(?:es|ed)?)\b.{0,80}\b(?:to|for)\b",
            quote[max(0, start - 120):end],
            re.I,
        ):
            return True
    return False


def _strict_local_fallback_sentiment(text: str) -> tuple[str, int] | None:
    """Assign only weak polarity entailed by one atomic fallback span."""
    if re.search(r"\b(?:rumou?r(?:ed)?|reportedly|may|might|could|considering)\b", text, re.I):
        return "neutral", 0
    positive = bool(re.search(
        r"\b(?:extend(?:s|ed|ing)?|renew(?:s|ed|ing)?)\b.{0,100}\b(?:deal|contract|agreement)\b|"
        r"\b(?:award(?:s|ed|ing)?|grant(?:s|ed|ing)?)\b.{0,100}\b(?:grant|award|funding)\b|"
        r"\bextinguish(?:es|ed|ing)?\b.{0,100}\b(?:debt|notes?|obligations?)\b|"
        r"\b(?:launch(?:e[sd]?|ing)|open(?:s|ed|ing)|ship(?:s|ped|ping)|publish(?:es|ed|ing))\b"
        r".{0,140}\b(?:product|service|platform|connectivity|data|results?)\b|"
        r"\b(?:favorable|positive)\b.{0,80}\b(?:data|results?|decision)\b",
        text,
        re.I,
    ))
    transaction_closing = bool(re.search(
        r"\bclos(?:e[sd]?|ing)\s+of\b.{0,100}"
        r"\b(?:investments?|acquisitions?|purchase(?: and sale)? agreements?)\b",
        text,
        re.I,
    ))
    negative = not transaction_closing and bool(re.search(
        r"\b(?:will not be available|unavailable|blocked?|downgrad(?:e[sd]?|ing)|"
        r"shut(?:s|ting)? down|lost|loss|adverse allegation)\b|"
        r"\bclos(?:e[sd]?|ing)\b.{0,80}\b(?:stores?|plants?|facilities?|offices?|sites?|mines?|operations?|businesses?|locations?)\b|"
        r"\b(?:stores?|plants?|facilities?|offices?|sites?|mines?|operations?|businesses?|locations?)\b"
        r".{0,80}\bclos(?:e[sd]?|ing)\b",
        text,
        re.I,
    ))
    if positive and negative:
        return None
    if positive:
        return "positive", 1
    if negative:
        return "negative", 1
    return "neutral", 0


def _boilerplate_sentence(text: str) -> bool:
    """Reject navigation and ingestion metadata without altering authoritative source offsets."""
    value = text.lstrip(" -\t")
    return bool(re.match(
        r"(?:related\s*:|related (?:links?|news|articles?)|also read|now read|read more|continue reading|see also|source \[(?:external|provider_body)|"
        r"image:|disclaimer:|click here|details? about .{0,100} can be found here|"
        r"enter a symbol|analyze any stock|free stock analysis|to track all upcoming earnings|"
        r"[A-Z][A-Za-z& -]{2,50} Software\s+(?:Manage|Streamline|Automate))\b",
        value,
        re.I,
    ))


def _forward_looking_boilerplate_sentence(text: str) -> bool:
    """Exclude hypothetical safe-harbor language from event extraction."""
    return bool(re.search(
        r"\bforward[- ]looking statements?\b|"
        r"^\s*[-*]\s*uncertainties surrounding\b|"
        r"\bactual results? (?:could|may|might|will) differ materially\b|"
        r"\brisks? and uncertainties\b.{0,120}\b(?:actual results?|future results?)\b|"
        r"\bpossibility\b.{0,120}\banticipated synergies\b.{0,80}\bnot (?:be )?realized\b|"
        r"\bdependence on\b.{0,100}\bnew product development\b|"
        r"\brisks? and uncertainties (?:related to|relating to)\b.{0,160}\bability\b|"
        r"\bchallenges? (?:related to|relating to)\b.{0,140}\bcompliance\b|"
        r"\bimpact of\b.{0,100}\bsubstantial indebtedness\b|"
        r"\b(?:no|cannot) assurance\b.{0,120}\b(?:will|would|may|might|could)\b",
        text,
        re.I,
    ))


def _entity_is_non_debtor_in_bankruptcy_context(
    entity: Mapping[str, Any] | None,
    text: str,
    mention_terms: Sequence[str],
) -> bool:
    """Distinguish an acquirer, buyer, or settlement party from the debtor.

    Bankruptcy frequently appears as provenance for an acquired asset or a
    resolved claim.  It is adverse only when the focal issuer is the debtor;
    the same token must not be transferred to a transaction counterparty.
    """
    if entity is None or not _entity_in_quote(entity, text, mention_terms):
        return False
    spans = _entity_mention_spans(entity, text, mention_terms)
    if not spans:
        return False
    cue_matches = list(re.finditer(r"\b(?:chapter 11|bankrupt(?:cy|cies)?|insolven(?:t|cy))\b", text, re.I))
    if not cue_matches:
        return False
    for start, end in spans:
        local = text[max(0, start - 60):min(len(text), end + 220)]
        if re.search(
            r"\b(?:file[sd]?|filing|petition(?:ed)?|enter(?:s|ed|ing)?|seek(?:s|ing)?|"
            r"initiat(?:e[sd]?|ing)|commenc(?:e[sd]?|ing)|"
            r"under|in)\b.{0,45}\b(?:chapter 11|bankrupt(?:cy)?|insolven(?:t|cy))\b|"
            r"\b(?:chapter 11|bankrupt(?:cy)?|insolven(?:t|cy))\b.{0,45}"
            r"\b(?:filing|petition|proceedings?|protection)\b",
            local,
            re.I,
        ):
            return False
    actor = any(
        re.search(
            r"\b(?:acquir(?:e|es|ed|ing)|buy(?:s|ing)?|bought|purchas(?:e|es|ed|ing)|"
            r"settle(?:s|d|ment)|recover(?:s|ed|y)|receiv(?:e|es|ed|ing))\b",
            text[max(0, start - 30):min(len(text), end + 140)],
            re.I,
        )
        for start, end in spans
    )
    if not actor:
        return False
    return any(
        min(abs(cue.start() - start), abs(cue.start() - end)) > 45
        for cue in cue_matches
        for start, end in spans
    )


def _distress_subject_is_other_entity(
    entity: Mapping[str, Any] | None,
    text: str,
    mention_terms: Sequence[str],
) -> bool:
    """Detect an explicitly named debtor distinct from the focal issuer."""
    if entity is None:
        return False
    subject_patterns = (
        r"(?i:\brestructuring of)\s+(?P<subject>[A-Z][A-Za-z0-9&.' -]{1,70})",
        r"(?P<subject>[A-Z][A-Za-z0-9&.' -]{1,70})'?s\s+"
        r"(?i:plan of reorganization|emergence from bankruptcy|chapter 11|bankruptcy)",
        r"(?i:\benable)\s+(?P<subject>[A-Z][A-Za-z0-9&.' -]{1,70})\s+"
        r"(?i:to\s+emerge\s+from\s+chapter 11)",
        r"(?P<subject>[A-Z][A-Za-z0-9&.' -]{1,70}),?\s+"
        r"(?i:which\s+is\s+currently\s+before\s+(?:the\s+)?(?:U\.S\.\s+)?bankruptcy court)",
    )
    for pattern in subject_patterns:
        for match in re.finditer(pattern, text):
            subject = match.group("subject").strip(" ,.-")
            if subject and not _entity_in_quote(entity, subject, mention_terms):
                return True
    return False


def _apply_event_supersession(
    statements: Sequence[Mapping[str, Any]],
    participations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Demote stale obstacle context after a controlling favorable resolution.

    News reports commonly explain that a hold or rejection existed after
    announcing that it was lifted or answered. Both facts remain preserved,
    but the resolved obstacle must not carry the same weight as a new active
    adverse regulator decision.
    """
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    by_entity: dict[str, list[Mapping[str, Any]]] = {}
    for row in participations:
        by_entity.setdefault(str(row["entity_id"]), []).append(row)
    resolved_entities: set[str] = set()
    for entity_id, rows in by_entity.items():
        for row in rows:
            statement = statement_by_id.get(str(row["statement_id"]), {})
            quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
            if (
                row.get("semantic_sentiment") == "positive"
                and statement.get("concept_leaf") in {
                    "clinical.regulatory_milestone",
                    "regulatory.action",
                }
                and re.search(
                    r"\b(?:lift(?:s|ed|ing)?|lifting|remov(?:e[sd]?|ing)|removal|resolv(?:e[sd]?|ing))\b"
                    r".{0,100}\bclinical hold\b|\bclear(?:s|ed|ing)?\b"
                    r".{0,100}\b(?:resume|begin enrolling)\b|"
                    r"\b(?:clearance|permission|authorization)\b.{0,100}\b(?:resume|begin)\b.{0,60}\b(?:trial|study|enrollment)\b|"
                    r"\b(?:resume|begin)\b.{0,60}\b(?:trial|study|enrollment)\b.{0,100}\b(?:clearance|permission|authorization)\b|"
                    r"\backnowledg(?:e[sd]?|ing)\b.{0,100}\bresubmission\b|"
                    r"\baccept(?:s|ed|ance)?\b.{0,80}\b(?:application|resubmission|submission)\b|"
                    r"\bfil(?:e[sd]?|ing)\b.{0,100}\b(?:response|submission)\b.{0,80}\b(?:FDA|EMA)\b|"
                    r"\b(?:FDA|EMA)\b.{0,160}\b(?:agreed?|acknowledg(?:e[sd]?|ing)|"
                    r"accept(?:s|ed|ance)?|consider(?:s|ed|ing)?)\b.{0,160}"
                    r"\b(?:complete class 2 response|class 2 resubmission)\b",
                    quote,
                    re.I,
                )
            ):
                resolved_entities.add(entity_id)
                break
    result: list[dict[str, Any]] = []
    for row in participations:
        clean = dict(row)
        entity_id = str(row["entity_id"])
        statement = statement_by_id.get(str(row["statement_id"]), {})
        quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        if (
            entity_id in resolved_entities
            and row.get("semantic_sentiment") == "negative"
            and statement.get("concept_leaf") in {
                "clinical.regulatory_milestone",
                "regulatory.action",
            }
        ):
            if re.search(
                r"\b(?:was|were|had been|previously)\b.{0,80}\b(?:placed )?on (?:a )?clinical hold\b|"
                r"\brelated\s*:",
                quote,
                re.I,
            ):
                clean["semantic_sentiment"] = "neutral"
                clean["sentiment_strength"] = 0
            elif re.search(r"\b(?:complete response letter|CRL)\b", quote, re.I):
                clean["semantic_sentiment"] = "neutral"
                clean["sentiment_strength"] = 0
            elif re.search(
                r"\b(?:concerns?|issues?)\b.{0,140}\bclinical hold\b.{0,140}\baddressed\b|"
                r"\bclinical hold\b.{0,140}\b(?:concerns?|issues?)\b.{0,140}\baddressed\b",
                quote,
                re.I,
            ):
                clean["semantic_sentiment"] = "neutral"
                clean["sentiment_strength"] = 0
        result.append(clean)
    return result


def _apply_intrinsic_event_tradeoffs(
    statements: Sequence[Mapping[str, Any]],
    participations: Sequence[Mapping[str, Any]],
    entities: Sequence[Mapping[str, Any]],
    mention_terms: Mapping[str, Sequence[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Represent material benefits and costs that coexist in one event clause."""
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    result_statements = [dict(row) for row in statements]
    result = [dict(row) for row in participations]
    entity_by_id = {str(row["entity_id"]): row for row in entities}
    seen: set[tuple[str, str, str]] = set()

    def add_opposite(row: Mapping[str, Any], sentiment: str, strength: int) -> None:
        key = (str(row["statement_id"]), str(row["entity_id"]), sentiment)
        if key in seen:
            return
        source_statement = statement_by_id[str(row["statement_id"])]
        clone = dict(source_statement)
        clone["statement_id"] = f"s{len(result_statements) + 1:04d}"
        result_statements.append(clone)
        clean = dict(row)
        clean["statement_id"] = clone["statement_id"]
        clean["semantic_sentiment"] = sentiment
        clean["sentiment_strength"] = strength
        result.append(clean)
        seen.add(key)

    for row in tuple(result):
        statement = statement_by_id.get(str(row["statement_id"]), {})
        concept = str(statement.get("concept_leaf") or "")
        quote = str((statement.get("evidence_spans") or [{}])[0].get("quote") or "")
        normalized = quote.casefold()
        sentiment = str(row.get("semantic_sentiment") or "")
        if concept == "corporate_transaction.asset_sale" and re.search(
            r"\b(?:hires? (?:an )?adviser|remaining .{0,40}assets?|is selling\b.{0,100}\bcould fetch)\b",
            normalized,
        ):
            if sentiment != "positive":
                add_opposite(row, "positive", 2)
            if sentiment != "negative":
                add_opposite(row, "negative", 2)
        elif (
            concept == "operations.capacity_change"
            and sentiment == "positive"
            and re.search(r"\b(?:doubles?|expands?)\b.{0,100}\bfleet\b", normalized)
            and (entity := entity_by_id.get(str(row["entity_id"]))) is not None
            and _entity_is_capacity_actor(
                entity,
                quote,
                mention_terms.get(str(row["entity_id"]), ()),
            )
        ):
            add_opposite(row, "negative", 2)
        elif concept == "capital.financing" and sentiment == "negative" and re.search(
            r"\bfinancing\b.{0,80}(?:\$|€|£)|(?:\$|€|£).{0,80}\bfinancing\b",
            quote,
            re.I,
        ) and not re.search(r"\b(?:warrant|exercise price|temporary reduction)\b", normalized):
            add_opposite(row, "positive", 2)
        elif concept == "capital.financing" and sentiment == "positive" and re.search(
            r"\b(?:conversion price|convertible|shares?|equity)\b",
            normalized,
        ) and not re.search(
            r"\b(?:repaid|repays?|paid off|fully paid|final payment|repayment)\b",
            normalized,
        ) and (
            (entity := entity_by_id.get(str(row["entity_id"]))) is None
            or _subsidiary_ipo_role(
                entity,
                quote,
                mention_terms.get(str(row["entity_id"]), ()),
            ) != "parent"
        ):
            add_opposite(row, "negative", 2)
        elif concept == "governance.shareholder_vote" and sentiment == "positive" and re.search(
            r"\breject\w*\b.{0,100}\b(?:ban|restrict|audit)\b",
            normalized,
        ):
            add_opposite(row, "negative", 2)
        elif concept == "legal.proceeding" and sentiment == "positive" and re.search(
            r"\b(?:derivative claims?|agreement to settle .{0,80}claims?)\b",
            normalized,
        ):
            add_opposite(row, "negative", 2)
        elif concept == "legal.proceeding" and sentiment == "positive" and re.search(
            r"\bdismiss(?:es|ed|al)?\b.{0,100}\b(?:due to|because of)\b.{0,80}"
            r"\b(?:lack|absence) of\b.{0,40}\b(?:fda|ema)?\s*approval\b|"
            r"\b(?:lack|absence) of\b.{0,40}\b(?:fda|ema)?\s*approval\b"
            r".{0,100}\bdismiss(?:es|ed|al)?\b",
            normalized,
        ):
            add_opposite(row, "negative", 2)
    return result_statements, result


def _entity_is_capacity_actor(
    entity: Mapping[str, Any],
    quote: str,
    mention_terms: Sequence[str],
) -> bool:
    cue = re.search(r"\b(?:doubles?|expands?)\b", quote, re.I)
    if cue is None:
        return False
    prefix = quote[:cue.start()]
    if _entity_in_quote(entity, prefix, mention_terms):
        return True
    normalized_prefix = f" {_normalize_alias(prefix)} "
    return any(
        _safe_alias(alias)
        and f" {alias} " in normalized_prefix
        for value in (*mention_terms, str(entity.get("display_name") or ""))
        for alias in _alias_variants(value)
    )


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    """Return exact source spans while preserving decimals and common abbreviations."""
    abbreviations = {
        "inc", "corp", "co", "ltd", "llc", "plc", "mr", "mrs", "ms", "dr",
        "st", "vs", "est", "adj", "prelim", "jan", "feb", "mar", "apr", "jun",
        "jul", "aug", "sep", "sept", "oct", "nov", "dec", "rev", "u.s", "a.m", "p.m",
    }
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
            abbreviation = bool(
                token_match
                and (
                    token_match.group(1).casefold().rstrip(".") in abbreviations
                    or re.search(r"(?:\b[A-Za-z]\.)+[A-Za-z]$", prefix)
                    or (
                        len(token_match.group(1)) == 1
                        and token_match.group(1).isupper()
                        and (
                            re.match(r"\s+[A-Z]\.", text[index + 1:])
                            or re.search(r"\b[A-Z]\.\s+[A-Z]$", prefix)
                        )
                    )
                )
            )
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


def _guidance_fact_context(
    semantic_rows: Sequence[tuple[int, str, str, int, int, str]],
    semantic_index: int,
    quote: str,
    lane_text: str,
    end: int,
) -> tuple[str, dict[str, Any] | None]:
    if semantic_index + 1 >= len(semantic_rows):
        return quote, None
    next_lane, next_field, _text, next_start, next_end, next_quote = semantic_rows[
        semantic_index + 1
    ]
    current_lane = semantic_rows[semantic_index][0]
    if next_lane != current_lane or "\n\n" in lane_text[end:next_start]:
        return quote, None
    if not re.match(
        r"\s*(?:analysts?|wall street|the street|consensus)\b",
        next_quote,
        re.I,
    ):
        return quote, None
    if not re.search(
        r"\b(?:sees|expects?|anticipates?|projects?|forecasts?|guidance|outlook)\b"
        r".{0,220}\b(?:EPS|earnings|loss|revenue|sales|EBITDA|margin)\b|"
        r"\b(?:EPS|earnings|loss|revenue|sales|EBITDA|margin)\b.{0,220}"
        r"\b(?:guidance|outlook|forecast)\b",
        quote,
        re.I,
    ):
        return quote, None
    return f"{quote} {next_quote}", {
        "source_field": next_field,
        "start": next_start,
        "end": next_end,
        "quote": next_quote,
    }


def _semantic_spans(text: str) -> list[tuple[int, int, str]]:
    """Keep rendered outlook table labels attached to their projection cells."""
    raw = [
        clause
        for start, end, quote in _sentence_spans(text)
        for clause in _temporal_clause_spans(start, end, quote, text)
    ]
    spans: list[tuple[int, int, str]] = []
    index = 0
    while index < len(raw):
        start, end, quote = raw[index]
        if index + 1 < len(raw):
            next_start, next_end, next_quote = raw[index + 1]
            same_line = "\n" not in text[end:next_start]
            table_label = "=" in quote and bool(re.search(
                r"\b(?:EPS|EBIT|EBITDA|revenue|sales|growth|margin|cash flow|tax rate|capex|interest expense|shares outstanding)\b",
                quote,
                re.I,
            ))
            if same_line and table_label and re.match(r"\s*Projection\s*=", next_quote, re.I):
                spans.append((start, next_end, text[start:next_end].strip()))
                index += 2
                continue
        spans.extend(_split_reported_metric_clauses(start, end, quote, text))
        index += 1
    return spans


_REPORTED_METRIC_CLAUSE_START_RE = re.compile(
    r"(?:adj(?:usted)?\.?\s+|diluted\s+|non-GAAP\s+)?"
    r"(?:EPS|earnings per share|revenues?|sales|EBITDA|net income|profit|net loss|loss)\b",
    re.I,
)
_REPORTED_METRIC_COMPARISON_CUE_RE = re.compile(
    r"\b(?:vs\.?|versus|up from|down from|compared (?:with|to))\b|"
    r"\b(?:beats?|miss(?:es|ed)?)\b.{0,80}\b(?:est\.?|estimate|consensus)\b",
    re.I,
)


def _split_reported_metric_clauses(
    start: int,
    end: int,
    quote: str,
    source_text: str,
) -> list[tuple[int, int, str]]:
    """Separate coordinated realized metrics so opposite directions both vote."""
    boundaries = [
        match
        for match in re.finditer(r",\s+", quote)
        if _REPORTED_METRIC_CLAUSE_START_RE.match(quote, match.end())
    ]
    if not boundaries:
        return [(start, end, quote)]
    offsets = [0, *(match.end() for match in boundaries), len(quote)]
    raw_chunks = [
        quote[offsets[index]:offsets[index + 1]]
        for index in range(len(offsets) - 1)
    ]
    if not all(
        _REPORTED_METRIC_COMPARISON_CUE_RE.search(chunk)
        for chunk in raw_chunks
    ):
        return [(start, end, quote)]
    result: list[tuple[int, int, str]] = []
    for index, raw_chunk in enumerate(raw_chunks):
        leading = len(raw_chunk) - len(raw_chunk.lstrip())
        chunk = raw_chunk.strip().rstrip(",")
        chunk_start = start + offsets[index] + leading
        chunk_end = chunk_start + len(chunk)
        result.append((chunk_start, chunk_end, source_text[chunk_start:chunk_end].strip()))
    return result


def _temporal_clause_spans(
    start: int,
    end: int,
    quote: str,
    source_text: str,
) -> list[tuple[int, int, str]]:
    """Separate historical subordinate clauses from their controlling event clause."""
    historical = r"(?:previously|historically|formerly|last (?:year|quarter|month))"
    leading = re.match(
        rf"\s*(?:after|having|following|despite|although)\b"
        rf"[^,;.!?]{{0,140}}\b{historical}\b[^,;.!?]{{0,100}},\s*",
        quote,
        re.I,
    )
    if leading:
        comma = quote.rfind(",", 0, leading.end())
        return _trimmed_source_spans(
            source_text,
            ((start, start + comma), (start + comma + 1, end)),
        )
    trailing = re.search(
        rf"\s+\b(?:after|(?<!the )following|despite|although)\b"
        rf"(?=[^,;.!?]{{0,100}}\b{historical}\b)",
        quote,
        re.I,
    )
    if trailing and trailing.start() > 0:
        split = start + trailing.start()
        return _trimmed_source_spans(source_text, ((start, split), (split, end)))
    return [(start, end, quote)]


def _trimmed_source_spans(
    text: str,
    bounds: Sequence[tuple[int, int]],
) -> list[tuple[int, int, str]]:
    result: list[tuple[int, int, str]] = []
    for start, end in bounds:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            result.append((start, end, text[start:end]))
    return result


def _financing_clause(text: str) -> bool:
    return bool(re.search(
        r"\b(?:public offering|secondary offering|private placement|"
        r"bond offering|notes? offering|debt financing|"
        r"selling (?:stockholders?|shareholders?|holders?)|"
        r"existing (?:stockholders?|shareholders?|holders?)|"
        r"offer(?:s|ed)?|offering|issu(?:e|es|ed|ing))\b"
        r".{0,140}\b(?:shares?|stock|units?|securities)\b",
        text,
        re.I,
    ))


def _financing_sentiment_text(
    text: str,
    start: int,
    end: int,
    event_text: str,
    entity: Mapping[str, Any],
    mention_terms: Sequence[str],
) -> str:
    """Attach an issuer-bound proceeds predicate to one secondary-sale event."""
    if not re.search(
        r"\b(?:secondary offering|selling (?:stockholders?|shareholders?|holders?)|"
        r"existing (?:stockholders?|shareholders?|holders?))\b",
        event_text,
        re.I,
    ):
        return event_text
    paragraph_start = text.rfind("\n\n", 0, start) + 2
    paragraph_end = text.find("\n\n", end)
    if paragraph_end < 0:
        paragraph_end = len(text)
    paragraph = text[paragraph_start:paragraph_end]
    rows = _sentence_spans(paragraph)
    financing_rows = [row for row in rows if _financing_clause(row[2])]
    aliases = {
        _normalize_alias(value)
        for value in (
            *mention_terms,
            str(entity.get("display_name") or ""),
            str(entity.get("ticker") or ""),
        )
        if _normalize_alias(value)
    }
    candidates: list[str] = []
    for _candidate_start, _candidate_end, candidate in rows:
        if not _financing_proceeds_qualifier(candidate):
            continue
        normalized = _normalize_alias(candidate)
        explicit_secondary = bool(re.search(
            r"\b(?:selling|existing) (?:stockholders?|shareholders?|holders?)\b|"
            r"\bshares? sold by\b|\bsecondary offering\b|\bholder sale\b|"
            r"\bthat holder sale\b|\bfrom (?:the |that )?resale\b",
            normalized,
        ))
        generic_subject = bool(re.search(
            r"\b(?:the )?(?:company|issuer|registrant|we|it)\b",
            normalized,
        ))
        explicit_entity = any(alias in normalized for alias in aliases if len(alias) >= 3)
        named_reporting_subject = re.search(
            r"(?:^|[,;]\s*)"
            r"([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,5})\s+"
            r"(?:said|stated|announced|noted|reported|confirmed|indicated|disclosed)\b",
            candidate,
        )
        foreign_named_subject = bool(
            named_reporting_subject
            and not any(
                alias in _normalize_alias(named_reporting_subject.group(1))
                for alias in aliases
                if len(alias) >= 3
            )
        )
        foreign_transaction = bool(re.search(
            r"\b(?:asset sale|sale of (?:assets?|business|operations?)|"
            r"acquisition|merger|contract)\b",
            normalized,
        ))
        unambiguous_event = len(financing_rows) == 1
        if (
            not foreign_transaction
            and not foreign_named_subject
            and (generic_subject or explicit_entity)
            and (unambiguous_event or explicit_secondary)
        ):
            candidates.append(candidate)
    return "\n".join((event_text, *candidates)) if candidates else event_text


def _financing_proceeds_qualifier(text: str) -> bool:
    return bool(re.search(
        r"\b(?:will|would|does|did)?\s*"
        r"(?:not receive|receive no|receives no|received no)\b"
        r".{0,80}\bproceeds\b|"
        r"\bno proceeds\b",
        text,
        re.I,
    ))


def _coordinated_guidance_fragment(text: str) -> bool:
    """Recognize a semicolon clause governed by the preceding guidance predicate."""
    return bool(re.match(
        r"\s*(?:(?:(?:Q[1-4]|FY)\s*\d{2,4}|full[- ]year|fiscal[- ]year)\b.{0,50})?"
        r"\b(?:EPS|earnings per share|rev\.?|revenue|total sales|sales|EBITDA|margin)\b"
        r".{0,120}\b(?:vs\.?|versus|compared (?:with|to))\b"
        r".{0,80}\b(?:est\.?|estimate|consensus)\b",
        text,
        re.I,
    ))


def _coordinated_result_fragment(text: str) -> bool:
    """Carry a reported-results predicate across a coordinated metric clause."""
    return bool(re.match(
        r"\s*(?:adjusted\s+|diluted\s+)?(?:EPS|earnings per share|revenues?|sales)\b"
        r".{0,120}\b(?:vs\.?|versus|compared (?:with|to))\b"
        r".{0,80}\b(?:est\.?|estimate|consensus)\b",
        text,
        re.I,
    ))


def _semantic_role(text: str, entity: Mapping[str, Any], concept: str) -> str:
    if concept == "corporate_transaction.acquisition":
        raw_names = {
            str(entity.get("display_name") or "").strip(),
            str(entity.get("ticker") or "").strip(),
        }
        raw_names.update(
            str(value).split(":", 1)[1].strip()
            for value in entity.get("identity_evidence", ())
            if str(value).startswith("issuer_alias:")
        )
        names = {
            variant
            for name in raw_names - {""}
            for variant in (name, *_alias_variants(name))
            if _safe_alias(_normalize_alias(variant))
            or _normalize_alias(variant) == _normalize_alias(entity.get("ticker") or "")
        }
        patterns = tuple(
            r"[^A-Za-z0-9]+".join(
                re.escape(token) for token in re.findall(r"[A-Za-z0-9]+", name)
            )
            for name in sorted(names - {""}, key=len, reverse=True)
            if re.findall(r"[A-Za-z0-9]+", name)
        )
        for name in patterns:
            if re.search(rf"{name}.{{0,80}}\b(?:agrees? to be|will be|is being|was) acquir", text, re.I): return "target"
            if re.search(rf"\bacquir.{{0,80}}{name}", text, re.I): return "target"
            if re.search(rf"{name}.{{0,80}}\bto be acquired\b", text, re.I): return "target"
            if re.search(rf"{name}.{{0,80}}\b(?:raises?|increases?|boosts?|sweetens?)\b.{{0,60}}\b(?:takeover )?(?:bid|offer)\b", text, re.I): return "acquirer"
            if re.search(rf"{name}.{{0,80}}\b(?:takeover )?(?:bid|offer)\b.{{0,40}}\bfor\b", text, re.I): return "acquirer"
            if re.search(rf"\b(?:takeover )?(?:bid|offer)\b.{{0,40}}\bfor\s+{name}\b", text, re.I): return "target"
            if re.search(rf"\b(?:firm|binding|cash|takeover)?\s*offer\b.{{0,40}}\bfor\s+{name}\b", text, re.I): return "target"
            if re.search(rf"\btakeover chatter\b.{{0,40}}\b(?:in|for)\s+{name}\b", text, re.I): return "target"
            if re.search(rf"{name}.*\b(?:acquir(?:e|es|ed|ing)|buys?|purchases?)\b", text, re.I): return "acquirer"
            if re.search(
                rf"{name}.{{0,100}}\bannounc(?:e|es|ed|ing)\b.{{0,80}}\bacquisitions?\b",
                text,
                re.I,
            ) and not re.search(
                r"\b(?:use|used|using)\b.{0,40}\bproceeds\b.{0,100}"
                r"\b(?:previously )?announced acquisition\b|"
                r"\bpreviously announced acquisition\b|"
                r"\bacquisition\b.{0,50}\bby\b.{0,80}\b(?:group|company|corp(?:oration)?|inc\.?|ltd\.?)\b",
                text,
                re.I,
            ):
                return "acquirer"
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
                r"\s+(?:incorporated|inc|corporation|corp|company|co|limited|ltd|plc|"
                r"holdings?|pharmaceuticals?|pharma|therapeutics?|technologies)$",
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
    return len(value) >= 5 and not _blocked_alias_content(value)


_BLOCKED_ALIAS_VALUES = frozenset({
    "america", "american", "block", "capital", "company", "credit",
    "discover", "early", "element", "energy", "equity", "financial",
    "global", "group", "guidance", "holdings", "international",
    "complete response letter",
    "investors", "mining", "national", "ordinary shares", "performance",
    "pharmaceuticals", "public", "securities", "solutions", "standard",
    "strategy", "target", "technology", "the company", "trading", "united",
    "western",
})


_CANDIDATE_BLOCKED_ALIAS_VALUES = frozenset({
    "capital", "company", "global", "group", "holdings", "international",
    "pharmaceutical", "pharmaceuticals", "solutions", "technology",
    "therapeutics",
})


_LEGAL_ROLE_ALIAS_TOKENS = frozenset({
    "business", "businesses", "co", "companies", "company", "corp",
    "corporation", "corporations", "enterprise", "enterprises", "firm",
    "firms", "group", "groups", "holding", "holdings", "inc",
    "incorporated", "issuer", "issuers", "limited", "ltd", "plc",
})


def _grammatical_legal_role_alias(value: str) -> bool:
    """Reject legal-role phrases that normalization made look like names.

    Apostrophe removal turns generic possessives such as ``the company's``
    into ``the company s``.  Those spans describe an already established
    issuer; they must never create a security identity.  The check is
    intentionally limited to legal-role vocabulary so distinctive possessive
    brands and full names such as ``New Company`` remain admissible.
    """
    tokens = value.split()
    if tokens[:1] == ["the"]:
        tokens = tokens[1:]
    if len(tokens) >= 2 and tokens[-1] == "s":
        tokens = tokens[:-1]
    return bool(tokens) and all(token in _LEGAL_ROLE_ALIAS_TOKENS for token in tokens)


def _blocked_alias_content(value: str) -> bool:
    return value in _BLOCKED_ALIAS_VALUES or _grammatical_legal_role_alias(value)


def _candidate_scoped_alias_variants(values: Iterable[str]) -> tuple[str, ...]:
    """Return exact public-name variants safe only inside provider scope.

    Global one-word alias matching remains deliberately strict. Within an
    already supplied provider candidate, a leading brand token can bind a
    headline that omits a recognized legal/descriptive tail. A three-token
    brand group may also shorten to its first brand. Arbitrary names, dates,
    and noisy leading prose are not shortened.
    """
    normalized_values = {
        normalized
        for value in values
        if (normalized := _normalize_alias(value))
    }
    variants = {
        alias
        for value in normalized_values
        for alias in _alias_variants(value)
    }
    descriptive_tail_tokens = {
        "co", "company", "corp", "corporation", "global", "group", "holding",
        "holdings", "inc", "incorporated", "industries", "industry", "limited",
        "ltd", "pharma", "pharmaceutical", "pharmaceuticals", "plc", "system",
        "systems", "technologies", "technology", "therapeutic", "therapeutics",
    }
    excluded_leading_tokens = {
        "american", "capital", "global", "international", "national", "north",
        "public", "standard", "the", "united",
    }
    for normalized in normalized_values:
        tokens = normalized.split()
        legal_tail = len(tokens) >= 2 and all(
            token in descriptive_tail_tokens for token in tokens[1:]
        )
        compound_group = (
            len(tokens) == 3 and tokens[-1] in {"group", "holding", "holdings"}
        )
        if legal_tail or compound_group:
            leading = tokens[0]
            if len(leading) >= 4 and leading not in excluded_leading_tokens:
                variants.add(leading)
    return tuple(sorted(
        variant
        for variant in variants
        if len(variant) >= 4
        and not _grammatical_legal_role_alias(variant)
        and variant not in _CANDIDATE_BLOCKED_ALIAS_VALUES
    ))
def _normalize_ticker_identifier(value: Any) -> str:
    raw = str(value or "").upper().strip()
    canadian = CANADIAN_EXCHANGE_PREFIX_RE.match(raw)
    if canadian:
        return f"{canadian.group('exchange').upper()}:{canadian.group('ticker').upper()}"
    return EXCHANGE_PREFIX_RE.sub("", raw)


def _dedupe_entities_by_id(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse multiple point-in-time symbols that resolve to one security."""
    by_id: dict[str, dict[str, Any]] = {}
    for source in sorted(rows, key=lambda row: (str(row.get("ticker") or ""), str(row.get("entity_id") or ""))):
        entity_id = str(source.get("entity_id") or "")
        if entity_id not in by_id:
            by_id[entity_id] = dict(source)
            continue
        current = by_id[entity_id]
        current["identity_evidence"] = sorted({
            *(str(value) for value in current.get("identity_evidence") or ()),
            *(str(value) for value in source.get("identity_evidence") or ()),
        })
    return sorted(by_id.values(), key=lambda row: (str(row.get("ticker") or ""), str(row["entity_id"])))


def _exchange_qualified_ticker_identifier(exchange: str, ticker: str) -> str:
    exchange_key = re.sub(r"\s+", "", exchange.upper())
    if exchange_key in {"TSX", "TSXV", "CSE"}:
        return f"{exchange_key}:{ticker.upper()}"
    return _normalize_ticker_identifier(ticker)
def _as_date(value: str) -> date | None:
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try: return date.fromisoformat(value[:10])
        except ValueError: return None

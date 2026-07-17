"""Versioned deterministic authority for user-facing news labels.

Ticker count defines scope only. Company news requires issuer or regulatory
evidence so editorial coverage cannot enter the priority company stream merely
because it links to one security. Keep SQL list output and Python detail output
derived from this module together whenever the contract changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CLASSIFICATION_VERSION = "news_rules_v1"

AI_TAGS = {"ai generated", "ai-generated", "benzai"}
ANALYST_CHANNELS = {
    "analyst color",
    "analyst ratings",
    "downgrades",
    "initiation",
    "price target",
    "reiteration",
    "upgrades",
}
MACRO_CHANNELS = {"econ #s", "economics", "macro economic events", "macro notification"}
ISSUER_EVENT_CHANNELS = {
    "asset sales",
    "buybacks",
    "contracts",
    "dividends",
    "earnings",
    "fda",
    "financing",
    "guidance",
    "m&a",
    "management",
    "offerings",
    "sec",
    "stock split",
}
DIRECT_RELEASE_DOMAINS = (
    "accesswire.com",
    "businesswire.com",
    "globenewswire.com",
    "prnewswire.com",
)
ISSUER_LANGUAGE = (
    "announced today",
    "company announced",
    "company reported",
    "company said",
    "issued a press release",
    "today announced",
)
EARNINGS_TITLE_LANGUAGE = (" eps ", " earnings", " revenue", " sales ")

TOPIC_CHANNELS: dict[str, set[str]] = {
    "analyst": ANALYST_CHANNELS,
    "buybacks": {"buybacks"},
    "commodities": {"commodities"},
    "contracts": {"contracts"},
    "cryptocurrency": {"cryptocurrency"},
    "dividends": {"dividends"},
    "earnings": {"earnings", "earnings beats", "earnings misses", "previews"},
    "financing": {"financing", "offerings"},
    "guidance": {"guidance"},
    "insider activity": {"insider trades"},
    "legal": {"legal", "regulations"},
    "macro": MACRO_CHANNELS | {"federal reserve", "government"},
    "management": {"management"},
    "market movement": {"after-hours center", "hot", "intraday update", "movers", "movers & shakers", "pre-market outlook"},
    "mergers & acquisitions": {"asset sales", "m&a"},
    "options": {"options"},
    "politics": {"politics"},
    "regulatory & clinical": {"fda", "sec"},
    "short interest": {"short sellers"},
    "technicals": {"technicals"},
}


@dataclass(frozen=True)
class NewsClassification:
    confidence: float
    evidence: tuple[str, ...]
    format: str
    is_company_news: bool
    kind: str
    origin: str
    scope: str
    topics: tuple[str, ...]
    version: str = CLASSIFICATION_VERSION

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        value["topics"] = list(self.topics)
        return value


def classify_news(row: dict[str, Any], ticker_count: int) -> NewsClassification:
    tags = _normalized_set(row.get("provider_tags"))
    channels = _normalized_set(row.get("channels"))
    links = tuple(str(value).strip().lower() for value in row.get("links") or [] if str(value).strip())
    author = str(row.get("author") or "").strip().lower()
    title = f" {str(row.get('title') or '').strip().lower()} "
    text = " ".join(
        (
            title,
            str(row.get("text") or row.get("normalized_full_text") or "")[:2_000].lower(),
        )
    )
    scope = "market_wide" if ticker_count == 0 else "single_ticker" if ticker_count == 1 else "multi_ticker"
    is_ai = bool(tags & AI_TAGS)
    is_analyst = bool(channels & ANALYST_CHANNELS)
    is_insights = author == "benzinga insights" or any(value.startswith("bzi-") for value in tags)
    is_why_moving = any(_is_why_moving(value) for value in tags)
    is_macro = bool(channels & MACRO_CHANNELS)
    is_halt = "trading halt" in title or "halt status" in title
    has_sec_link = any("sec.gov" in value for value in links)
    has_release_link = any(domain in value for value in links for domain in DIRECT_RELEASE_DOMAINS)
    has_issuer_language = any(phrase in text for phrase in ISSUER_LANGUAGE)
    has_issuer_event = bool(channels & ISSUER_EVENT_CHANNELS)
    is_newsdesk = author in {"benzinga", "benzinga newsdesk"}
    is_earnings_flash = (
        ticker_count == 1
        and is_newsdesk
        and "earnings" in channels
        and any(phrase in title for phrase in EARNINGS_TITLE_LANGUAGE)
        and not is_why_moving
        and not is_analyst
    )
    excluded_from_issuer = is_ai or is_analyst or is_insights or is_why_moving or is_macro or is_halt
    is_regulatory = ticker_count == 1 and has_sec_link and not excluded_from_issuer
    is_issuer = ticker_count == 1 and not excluded_from_issuer and (
        is_earnings_flash
        or (has_release_link and has_issuer_language)
        or (has_issuer_event and has_issuer_language)
    )

    if is_ai:
        kind, origin, format_name, confidence = "ai", "automated", "ai_generated", 0.99
    elif is_analyst:
        kind, origin, format_name, confidence = "analyst", "analyst", "analyst_action", 0.99
    elif is_insights:
        kind, origin, format_name, confidence = "insights", "automated", "insights", 0.98
    elif is_why_moving:
        kind, origin, format_name, confidence = "why_moving", "editorial", "why_moving", 0.99
    elif is_halt:
        kind, origin, format_name, confidence = "market", "regulatory", "trading_halt", 0.98
    elif is_regulatory:
        kind, origin, format_name, confidence = "regulatory", "regulatory", "regulatory_filing", 0.96
    elif is_issuer:
        kind, origin = "company", "issuer"
        format_name = "earnings_flash" if is_earnings_flash else "company_announcement"
        confidence = 0.96 if has_release_link and has_issuer_language else 0.93
    elif is_macro:
        kind, origin, format_name, confidence = "market", "regulatory", "macro_release", 0.98
    elif ticker_count > 1:
        kind, origin, format_name, confidence = "multi", "editorial", "multi_company_coverage", 0.88
    elif author:
        kind, origin, format_name, confidence = "editorial", "editorial", "editorial_coverage", 0.82
    else:
        kind, origin, format_name, confidence = "market", "unknown", "general", 0.65

    evidence: list[str] = [scope.replace("_", " ")]
    if is_ai:
        evidence.append("AI provider format")
    elif is_analyst:
        evidence.append("analyst channel")
    elif is_insights:
        evidence.append("Insights provider format")
    elif is_why_moving:
        evidence.append("why-moving provider format")
    elif is_halt:
        evidence.append("trading-halt language")
    elif is_regulatory:
        evidence.append("regulatory source")
    elif is_issuer:
        if has_release_link:
            evidence.append("direct release source")
        if has_issuer_language:
            evidence.append("issuer announcement language")
        if is_earnings_flash:
            evidence.append("structured earnings report")
    elif is_macro:
        evidence.append("macroeconomic channel")
    elif ticker_count > 1:
        evidence.append("multiple linked companies")
    elif author:
        evidence.append("named editorial author")

    topics = [topic for topic, values in TOPIC_CHANNELS.items() if channels & values]
    if is_why_moving:
        topics.append("why moving")
    if is_ai:
        topics.append("AI generated")
    return NewsClassification(
        confidence=confidence,
        evidence=tuple(dict.fromkeys(evidence)),
        format=format_name,
        is_company_news=is_issuer or is_regulatory,
        kind=kind,
        origin=origin,
        scope=scope,
        topics=tuple(dict.fromkeys(topics)),
    )


def classify_news_kind(row: dict[str, Any], ticker_count: int) -> str:
    return classify_news(row, ticker_count).kind


def news_classification_sql(ticker_links_sql: str, alias: str = "n") -> dict[str, str]:
    channels = f"{alias}.channels"
    tags = f"{alias}.provider_tags"
    links = f"{alias}.links"
    title = f"{alias}.title"
    body = f"{alias}.normalized_full_text"
    author = f"{alias}.author"
    count = f"length({ticker_links_sql})"
    ai = _sql_array_intersection(tags, AI_TAGS)
    analyst = _sql_array_intersection(channels, ANALYST_CHANNELS)
    insights = f"(lowerUTF8(trimBoth({author})) = 'benzinga insights' OR arrayExists(value -> startsWith(lowerUTF8(trimBoth(value)), 'bzi-'), {tags}))"
    why_moving = f"arrayExists(value -> (position(lowerUTF8(value), 'why') > 0 AND position(lowerUTF8(value), 'mov') > 0), {tags})"
    macro = _sql_array_intersection(channels, MACRO_CHANNELS)
    halt = f"(positionCaseInsensitiveUTF8({title}, 'trading halt') > 0 OR positionCaseInsensitiveUTF8({title}, 'halt status') > 0)"
    sec_link = f"arrayExists(value -> positionCaseInsensitiveUTF8(value, 'sec.gov') > 0, {links})"
    release_link = "(" + " OR ".join(
        f"arrayExists(value -> positionCaseInsensitiveUTF8(value, '{domain}') > 0, {links})" for domain in DIRECT_RELEASE_DOMAINS
    ) + ")"
    text = f"concat(ifNull({title}, ''), ' ', substring(ifNull({body}, ''), 1, 2000))"
    issuer_language = "(" + " OR ".join(
        f"positionCaseInsensitiveUTF8({text}, '{phrase}') > 0" for phrase in ISSUER_LANGUAGE
    ) + ")"
    issuer_event = _sql_array_intersection(channels, ISSUER_EVENT_CHANNELS)
    newsdesk = f"lowerUTF8(trimBoth({author})) IN ('benzinga', 'benzinga newsdesk')"
    earnings_title = "(" + " OR ".join(
        f"positionCaseInsensitiveUTF8(concat(' ', {title}, ' '), '{phrase}') > 0" for phrase in EARNINGS_TITLE_LANGUAGE
    ) + ")"
    earnings_channel = _sql_array_intersection(channels, {"earnings"})
    earnings_flash = f"({count} = 1 AND {newsdesk} AND {earnings_channel} AND {earnings_title} AND NOT {why_moving} AND NOT {analyst})"
    excluded = f"({ai} OR {analyst} OR {insights} OR {why_moving} OR {macro} OR {halt})"
    regulatory = f"({count} = 1 AND {sec_link} AND NOT {excluded})"
    issuer = f"({count} = 1 AND NOT {excluded} AND ({earnings_flash} OR ({release_link} AND {issuer_language}) OR ({issuer_event} AND {issuer_language})))"
    scope = f"multiIf({count} = 0, 'market_wide', {count} = 1, 'single_ticker', 'multi_ticker')"
    kind = f"multiIf({ai}, 'ai', {analyst}, 'analyst', {insights}, 'insights', {why_moving}, 'why_moving', {halt}, 'market', {regulatory}, 'regulatory', {issuer}, 'company', {macro}, 'market', {count} > 1, 'multi', notEmpty(trimBoth({author})), 'editorial', 'market')"
    origin = f"multiIf({ai}, 'automated', {analyst}, 'analyst', {insights}, 'automated', {why_moving}, 'editorial', {halt}, 'regulatory', {regulatory}, 'regulatory', {issuer}, 'issuer', {macro}, 'regulatory', {count} > 1, 'editorial', notEmpty(trimBoth({author})), 'editorial', 'unknown')"
    format_name = f"multiIf({ai}, 'ai_generated', {analyst}, 'analyst_action', {insights}, 'insights', {why_moving}, 'why_moving', {halt}, 'trading_halt', {regulatory}, 'regulatory_filing', {issuer}, if({earnings_flash}, 'earnings_flash', 'company_announcement'), {macro}, 'macro_release', {count} > 1, 'multi_company_coverage', notEmpty(trimBoth({author})), 'editorial_coverage', 'general')"
    confidence = f"multiIf({ai} OR {analyst} OR {why_moving}, 0.99, {insights} OR {halt} OR {macro}, 0.98, {regulatory}, 0.96, {issuer}, if({release_link} AND {issuer_language}, 0.96, 0.93), {count} > 1, 0.88, notEmpty(trimBoth({author})), 0.82, 0.65)"
    company = f"({issuer} OR {regulatory})"
    issuer_evidence = f"arrayFilter(value -> notEmpty(value), [{_sql_if(release_link, 'direct release source')}, {_sql_if(issuer_language, 'issuer announcement language')}, {_sql_if(earnings_flash, 'structured earnings report')}])"
    decision_evidence = f"multiIf({ai}, ['AI provider format'], {analyst}, ['analyst channel'], {insights}, ['Insights provider format'], {why_moving}, ['why-moving provider format'], {halt}, ['trading-halt language'], {regulatory}, ['regulatory source'], {issuer}, {issuer_evidence}, {macro}, ['macroeconomic channel'], {count} > 1, ['multiple linked companies'], notEmpty(trimBoth({author})), ['named editorial author'], [])"
    evidence = f"arrayConcat([replaceAll({scope}, '_', ' ')], {decision_evidence})"
    topics = _sql_topics(channels, why_moving, ai)
    return {
        "company": company,
        "confidence": confidence,
        "evidence": evidence,
        "format": format_name,
        "kind": kind,
        "origin": origin,
        "scope": scope,
        "topics": topics,
    }


def _normalized_set(value: Any) -> set[str]:
    return {str(item).strip().lower() for item in value or [] if str(item).strip()}


def _is_why_moving(value: str) -> bool:
    compact = "".join(character for character in value.lower() if character.isalnum())
    return "why" in compact and "mov" in compact


def _sql_array_intersection(column: str, values: set[str]) -> str:
    choices = ", ".join(f"'{value}'" for value in sorted(values))
    return f"arrayExists(value -> lowerUTF8(trimBoth(value)) IN ({choices}), {column})"


def _sql_if(condition: str, value: str) -> str:
    return f"if({condition}, '{value}', '')"


def _sql_topics(channels: str, why_moving: str, ai: str) -> str:
    values = [
        _sql_if(_sql_array_intersection(channels, channel_values), topic)
        for topic, channel_values in TOPIC_CHANNELS.items()
    ]
    values.extend((_sql_if(why_moving, "why moving"), _sql_if(ai, "AI generated")))
    return f"arrayFilter(value -> notEmpty(value), [{', '.join(values)}])"

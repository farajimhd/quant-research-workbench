from __future__ import annotations

from .deterministic_v6_config import PatternRule


DETERMINISTIC_V7_VERSION = "news_deterministic_v7_candidate_1"


# Article roles are structural publication types.  Rules intentionally inspect
# the headline and provider metadata, never later prices or human annotations.
ROLE_RULES: tuple[PatternRule, ...] = (
    PatternRule("why_moving", (
        r"\bwhy\s+(?:is|are|did)\b.{0,120}\b(?:moving|up|down|rising|falling|surging|sliding)\b",
        r"\bwhat(?:'s| is)\s+going\s+on\s+with\b",
        r"\bstrength\s+attributed\s+to\b",
    ), "why_moving_followup"),
    PatternRule("mover_recap", (
        r"\b\d+\s+(?:biggest\s+)?(?:mid[- ]?day|pre[- ]?market|after[- ]?hours?)?\s*(?:stock\s+)?(?:gainers|losers|movers)\b",
        r"\b(?:biggest|top)\s+(?:stock\s+)?(?:gainers|losers|movers)\b",
        r"\b(?:pre[- ]?market|after[- ]?hours?|mid[- ]?day|morning)\s+(?:top\s+)?(?:percentage\s+)?(?:gainers|losers|movers)\b",
        r"\bstocks?\s+(?:hitting|hit)\s+(?:new\s+)?52[- ]week\s+(?:highs|lows)\b",
        r"\b(?:volume|option)\s+movers\b",
        r"\bmorning\s+market\s+(?:gainers|losers|movers)\b",
        r"\bhere\s+are\s+\d+\s+stocks\s+moving\s+premarket\b",
        r"\band\s+other\s+big\s+stocks\s+moving\s+(?:higher|lower)\b",
        r"\band\s+(?:some\s+)?other\s+big\s+(?:gainers|losers)\b",
        r"\btop\s+pre[- ]?market\s+(?:nasdaq\s+)?(?:gainers|losers)\b",
        r"\bstocks?\s+moving\s+in\s+(?:monday|tuesday|wednesday|thursday|friday)'?s\b",
    ), "mover_recap"),
    PatternRule("market_roundup", (
        r"\bmarket\s+(?:update|wrap|recap)\b",
        r"\bmarkets?\s+(?:today|this week|rise|rises|fall|falls|gather|close|closes)\b",
        r"\b(?:u\.?s\.?\s+)?stock\s+futures\b",
        r"\b(?:dow|nasdaq|s&p\s*500|wall street)\b.{0,100}\b(?:jumps?|falls?|drops?|rall(?:y|ies)|closes?|waits?|hits?)\b",
        r"\ba\s+peek\s+into\s+the\s+markets\b",
        r"\b(?:stocks?|equities)\s+(?:higher|lower|mixed)\b",
        r"\b(?:crude oil|gold|bitcoin)\b.{0,80}\b(?:moves?|falls?|rises?|jumps?)\b",
        r"\bthe\s+market\s+in\s+\d+\s+minutes\b",
        r"\b(?:daily|weekly)\s+(?:biotech|technology|energy)\s+pulse\b",
        r"\b(?:retail sales|earnings|analyst|ratings?)\s+roundup\b",
        r"\bbenzinga'?s\s+top\s+(?:upgrades|downgrades)\b",
        r"\btop\s+\d+\s+(?:upgrades|downgrades)\b",
        r"\bleading\s+and\s+lagging\s+sectors\b",
        r"\b(?:m&a|takeover)\s+chatter\b",
        r"\bcompany\s+news\b.{0,80}\bcorporate\s+summary\b",
        r"\btop\s+performing\s+industries\b",
        r"\b(?:pre[- ]?market\s+)?primer\b",
        r"\bthis\s+week\s+in\s+the\s+markets\b",
        r"\b(?:markets?|stocks?)\s+(?:rally|rallies|rallied|mostly higher|poised to open|jaded|suffer)\b",
        r"\binvestor\s+sentiment\b.{0,100}\b(?:dow|treasury|market|stocks?)\b",
        r"\bauto\s+sales\s+roundup\b",
        r"\bstocks?\s+to\s+watch\b",
    ), "market_roundup"),
    PatternRule("automated_summary", (
        r"\binsights?\s+into\b.{0,120}\bperformance\b",
        r"\bindustry\s+comparison\b",
        r"\bperformance\s+versus\s+peers\b",
        r"\bautomatically\s+generated\b",
        r"\bbenzinga\s+insights\b",
        r"\btop\s+trending\s+tickers\b",
        r"\boption\s+alert\b",
    ), "automated_summary"),
    PatternRule("analyst_event", (
        r"\b(?:analyst|brokerage|research\s+firm|zacks)\b.{0,180}\b(?:maintains?|reiterates?|upgrades?|downgrades?|initiates?|resumes?|price\s+target|rating|valuation)\b",
        r"\b(?:maintains?|reiterates?|upgrades?|downgrades?|initiates?|resumes?)\b.{0,140}\b(?:buy|sell|hold|overweight|underweight|outperform|underperform|neutral)\b",
        r"\b(?:raises?|lowers?|cuts?)\s+(?:its\s+)?price\s+target\b",
        r"\banalyst\s+blog\b",
        r"\bresearch\s+report\s+on\b",
    ), "analyst_event"),
    PatternRule("preview", (
        r"\bearnings\s+(?:prediction\s+market\s+)?(?:preview|outlook)\b",
        r"\bweekly\s+preview\b",
        r"\bearnings\s+scheduled\s+for\b",
        r"\b(?:scheduled|expected)\s+to\s+report\s+(?:quarterly\s+)?(?:earnings|results)\b",
        r"\b(?:ahead\s+of|before)\b.{0,80}\bearnings\b",
        r"\bwill\b.{0,80}\b(?:surprise|report)\b.{0,50}\bearnings\b",
        r"\bwhat\s+(?:investors|traders|analysts)\s+(?:need\s+to\s+know|are\s+saying)\b",
        r"\bweek\s+ahead\b",
        r"\bearnings\s+season\b.{0,80}\b(?:preview|ahead)\b",
    ), "preview"),
    PatternRule("regulatory_event", (
        r"\b(?:fda|food and drug administration)\b.{0,120}\b(?:approve[sd]?|approval|rejects?|rejection|grants?|accepts?|meeting|notification|clinical hold|complete response letter)\b",
        r"\b(?:receives?|receipt of|issued?)\b.{0,100}\b(?:fda approval|complete response letter|clinical hold)\b",
        r"\b(?:primary|co-primary)\s+endpoint\b",
        r"\b(?:nasdaq|nyse)\b.{0,120}\b(?:notification|noncompliance|delisting|minimum bid)\b",
        r"\b(?:subpoena|grand jury|antitrust|bankruptcy court|chapter\s+11)\b",
        r"\b(?:sec|securities and exchange commission)\b.{0,100}\b(?:filing|investigation|charges?|settlement)\b",
    ), "regulatory_event"),
    PatternRule("primary_event", (
        r"\b(?:announces?|reports?|launches?|receives?|secures?|signs?|enters?|acquires?|merges?|prices?|completes?|closes?|reaffirms?|awarded?)\b",
        r"\b(?:quarterly results|clinical results|offering|financing|acquisition|merger|contract|partnership|guidance|earnings call transcript)\b",
        r"\b(?:shares?|stock)\s+are\s+trading\s+(?:higher|lower)\s+after\s+the\s+company\b",
        r"\b(?:meets?|misses?|surpasses?)\b.{0,80}\b(?:endpoint|expectations?|estimates?)\b",
    ), "primary_event"),
)


HIGH_VALUE_EVENT_PATTERNS: tuple[str, ...] = (
    r"\b(?:beat|miss(?:ed|es)?|above|below)\b.{0,80}\b(?:estimate|expectation|consensus)s?\b",
    r"\b(?:raise[sd]?|lower(?:ed|s)?|cut|reaffirm(?:ed|s)?|withdraw(?:n|s)?)\b.{0,80}\b(?:guidance|outlook|forecast)\b",
    r"\b(?:primary|key)\s+endpoint\b",
    r"\b(?:fda|regulator)\b.{0,100}\b(?:approv|reject|grant|accept|hold|response letter)\w*\b",
    r"\b(?:offering|private placement|at[- ]the[- ]market|share repurchase|buyback|dividend)\b",
    r"\b(?:acqui(?:re|sition)|merger|definitive agreement|takeover|go private)\b",
    r"\b(?:contract|purchase order|partnership|collaboration|license agreement)\b",
    r"\b(?:investigation|subpoena|lawsuit|litigation|settlement|recall|deaths?)\b",
    r"\b(?:bankruptcy|chapter\s+(?:7|11)|going concern|delisting|noncompliance)\b",
    r"\b(?:restructuring|layoffs?|workforce reduction|shutdown|production halt)\b",
    r"\b(?:revenue|sales|earnings|eps|net income|operating profit)\b.{0,90}\b(?:grew|rose|fell|declined|increased|decreased|record|loss)\b",
)


ISSUER_DIRECT_CHANNELS = frozenset({
    "contracts", "dividends", "ipos", "m&a", "offerings", "press releases",
})

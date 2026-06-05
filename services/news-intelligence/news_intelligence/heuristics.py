from __future__ import annotations

import re

from .schemas import NewsArticleForClassification, TickerImpact


POSITIVE_TERMS = {
    "beat",
    "beats",
    "upgrade",
    "upgraded",
    "raises",
    "raised",
    "approval",
    "approved",
    "contract",
    "record",
    "surge",
    "rally",
    "acquisition",
    "buyout",
    "guidance raised",
}

NEGATIVE_TERMS = {
    "miss",
    "misses",
    "downgrade",
    "downgraded",
    "cuts",
    "cut",
    "offering",
    "bankruptcy",
    "investigation",
    "lawsuit",
    "halt",
    "delisting",
    "guidance cut",
}

EVENT_PATTERNS = [
    ("earnings", re.compile(r"\b(earnings|eps|revenue|quarterly results|guidance)\b", re.I)),
    ("analyst_rating", re.compile(r"\b(upgrade|downgrade|price target|initiates|maintains)\b", re.I)),
    ("capital_markets", re.compile(r"\b(offering|atm|registered direct|private placement|warrant)\b", re.I)),
    ("mna", re.compile(r"\b(acquire|acquisition|merger|buyout|takeover)\b", re.I)),
    ("fda_biotech", re.compile(r"\b(fda|phase 1|phase 2|phase 3|clinical|trial|approval)\b", re.I)),
    ("legal_regulatory", re.compile(r"\b(sec|doj|lawsuit|investigation|settlement|fine)\b", re.I)),
    ("macro_geopolitical", re.compile(r"\b(fed|inflation|cpi|war|tariff|sanction|election|japan|china)\b", re.I)),
    ("crypto", re.compile(r"\b(bitcoin|ethereum|crypto|blockchain|btc|eth)\b", re.I)),
]


def heuristic_sentiment(text: str) -> tuple[str, float, float]:
    lower = text.lower()
    positive = sum(1 for term in POSITIVE_TERMS if term in lower)
    negative = sum(1 for term in NEGATIVE_TERMS if term in lower)
    if positive == negative:
        return "neutral", 0.0, 0.35 if positive == 0 else 0.45
    score = (positive - negative) / max(positive + negative, 1)
    label = "positive" if score > 0 else "negative"
    return label, max(min(score, 1.0), -1.0), min(0.85, 0.45 + 0.1 * abs(positive - negative))


def heuristic_event_type(text: str) -> str:
    for label, pattern in EVENT_PATTERNS:
        if pattern.search(text):
            return label
    return "uncategorized"


def heuristic_materiality(article: NewsArticleForClassification, text: str, event_type: str) -> float:
    score = 0.15
    if article.tickers:
        score += 0.25
    if event_type in {"earnings", "analyst_rating", "capital_markets", "mna", "fda_biotech"}:
        score += 0.3
    if len(text) > 500:
        score += 0.1
    if article.scanner_relevance == "high":
        score += 0.2
    return min(score, 1.0)


def heuristic_urgency(text: str) -> float:
    lower = text.lower()
    score = 0.2
    if any(term in lower for term in ["breaking", "halt", "resumes", "offering", "fda", "merger"]):
        score += 0.35
    if any(term in lower for term in ["premarket", "after-hours", "today"]):
        score += 0.15
    return min(score, 1.0)


def ticker_impacts(article: NewsArticleForClassification, label: str, score: float, confidence: float) -> list[TickerImpact]:
    tickers = article.tickers or article.insight_tickers
    return [
        TickerImpact(
            ticker=ticker.upper(),
            sentiment_label=label,
            direction_score=score,
            confidence=confidence,
            rationale="direct provider ticker mention",
        )
        for ticker in tickers
        if ticker
    ]


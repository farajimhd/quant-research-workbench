from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schemas import IntelligenceResponse, NewsArticleForClassification, TickerImpact


SUPERVISOR_VERSION = "codex-silver-supervisor-v1"


@dataclass(frozen=True)
class EventRule:
    event_type: str
    event_subtype: str
    pattern: re.Pattern[str]
    sentiment_label: str
    base_materiality: float
    base_urgency: float
    labels: tuple[str, ...]


EVENT_RULES: tuple[EventRule, ...] = (
    EventRule("analyst_rating", "downgrade", re.compile(r"\b(downgrade|downgrades|lowered to sell|cut to sell)\b", re.I), "negative", 0.78, 0.72, ("analyst_action", "rating_cut")),
    EventRule("analyst_rating", "upgrade", re.compile(r"\b(upgrade|upgrades|raised to buy|initiates at buy|initiated at buy)\b", re.I), "positive", 0.76, 0.70, ("analyst_action", "rating_raise")),
    EventRule("analyst_rating", "price_target_raise", re.compile(r"\b(raises|raised|lifts|boosts).{0,60}\b(price target|pt)\b|\b(price target|pt).{0,60}\b(raised|lifted|boosted)\b", re.I), "positive", 0.70, 0.66, ("analyst_action", "target_raise")),
    EventRule("analyst_rating", "price_target_cut", re.compile(r"\b(cuts|cut|lowers|lowered).{0,60}\b(price target|pt)\b|\b(price target|pt).{0,60}\b(cut|lowered)\b", re.I), "negative", 0.70, 0.66, ("analyst_action", "target_cut")),
    EventRule("earnings", "guidance_raise", re.compile(r"\b(raises|raised|boosts|increases).{0,50}\b(guidance|outlook|forecast)\b", re.I), "positive", 0.84, 0.76, ("earnings", "guidance_raise")),
    EventRule("earnings", "guidance_cut", re.compile(r"\b(cuts|cut|lowers|reduced|withdraws).{0,50}\b(guidance|outlook|forecast)\b", re.I), "negative", 0.86, 0.78, ("earnings", "guidance_cut")),
    EventRule("earnings", "results", re.compile(r"\b(earnings|eps|revenue|quarterly results|q[1-4] results|fiscal .{0,20} results)\b", re.I), "neutral", 0.72, 0.62, ("earnings", "results")),
    EventRule("capital_markets", "offering", re.compile(r"\b(public offering|registered direct|private placement|at-the-market|atm offering|prices offering|share offering|warrant)\b", re.I), "negative", 0.82, 0.82, ("capital_markets", "dilution_risk")),
    EventRule("capital_markets", "buyback", re.compile(r"\b(buyback|repurchase program|share repurchase)\b", re.I), "positive", 0.68, 0.46, ("capital_markets", "buyback")),
    EventRule("merger_acquisition", "takeover", re.compile(r"\b(acquire|acquires|acquisition|merger|buyout|takeover|to be acquired|strategic alternatives)\b", re.I), "positive", 0.88, 0.86, ("deal", "corporate_action")),
    EventRule("fda_biotech", "approval", re.compile(r"\b(fda approval|approved by the fda|clearance|granted approval|positive phase|meets primary endpoint)\b", re.I), "positive", 0.88, 0.84, ("biotech", "regulatory_positive")),
    EventRule("fda_biotech", "clinical_risk", re.compile(r"\b(clinical hold|complete response letter|crl|fails|failed|missed primary endpoint|trial halt)\b", re.I), "negative", 0.90, 0.86, ("biotech", "regulatory_negative")),
    EventRule("fda_biotech", "trial_update", re.compile(r"\b(fda|phase 1|phase 2|phase 3|clinical trial|pivotal trial|drug candidate)\b", re.I), "neutral", 0.70, 0.58, ("biotech", "clinical_update")),
    EventRule("legal_regulatory", "investigation", re.compile(r"\b(sec|doj|ftc|fda|investigation|probe|subpoena|lawsuit|class action|settlement|fine|penalty)\b", re.I), "negative", 0.70, 0.56, ("legal", "regulatory")),
    EventRule("product_contract", "contract", re.compile(r"\b(contract|partnership|collaboration|supply agreement|purchase order|customer win|launches|product launch)\b", re.I), "positive", 0.66, 0.48, ("commercial_update", "contract")),
    EventRule("insider_ownership", "stake", re.compile(r"\b(insider buying|insider buys|13d|13g|stake|activist investor|takes position)\b", re.I), "positive", 0.62, 0.48, ("ownership", "positioning")),
    EventRule("macro_geopolitical", "macro", re.compile(r"\b(fed|fomc|inflation|cpi|ppi|jobs report|nonfarm|treasury yield|tariff|sanction|war|election|government shutdown|japan|china)\b", re.I), "neutral", 0.48, 0.40, ("macro", "context")),
    EventRule("crypto", "crypto_market", re.compile(r"\b(bitcoin|ethereum|crypto|blockchain|btc|eth|solana|dogecoin|xrp)\b", re.I), "neutral", 0.48, 0.44, ("crypto", "cross_asset")),
)

POSITIVE_TERMS = re.compile(r"\b(beat|beats|surge|jumps|rallies|record high|raises|approval|approved|contract|partnership|buyout|upgrade)\b", re.I)
NEGATIVE_TERMS = re.compile(r"\b(miss|misses|falls|drops|plunges|downgrade|cuts|offering|bankruptcy|halt|delisting|lawsuit|investigation)\b", re.I)


class CodexSilverSupervisor:
    """Deterministic silver labeler for offline experiments, not a human-label source."""

    def classify(self, article: NewsArticleForClassification) -> IntelligenceResponse:
        text = article.classification_text(8000)
        rule = match_event_rule(text)
        sentiment_label, sentiment_score, sentiment_confidence = infer_sentiment(text, rule)
        materiality = adjust_materiality(rule.base_materiality, article, text) if rule else fallback_materiality(article, text)
        urgency = adjust_urgency(rule.base_urgency, text) if rule else fallback_urgency(text)
        event_type = rule.event_type if rule else "other"
        event_subtype = rule.event_subtype if rule else ""
        labels = sorted(set(list(rule.labels if rule else ("other",)) + content_labels(article, text)))
        return IntelligenceResponse(
            stack_version=SUPERVISOR_VERSION,
            taxonomy_version="news-taxonomy-v1",
            prompt_version="codex-supervision-rules-v1",
            model_stack=[SUPERVISOR_VERSION],
            summary=build_summary(article),
            sentiment_label=sentiment_label,
            sentiment_score=sentiment_score,
            sentiment_confidence=sentiment_confidence,
            event_type=event_type,
            event_subtype=event_subtype,
            materiality_score=materiality,
            novelty_score=0.0,
            urgency_score=urgency,
            time_horizon=infer_time_horizon(event_type, urgency, materiality),
            affected_tickers=build_ticker_impacts(article, sentiment_label, sentiment_score, sentiment_confidence),
            content_completeness=infer_content_completeness(article),
            evidence_basis=infer_evidence_basis(article),
            labels=labels,
            rationale=build_rationale(rule, article, text),
            raw_outputs={"supervisor": {"version": SUPERVISOR_VERSION, "matched_rule": rule.event_subtype if rule else ""}},
        )


def match_event_rule(text: str) -> EventRule | None:
    for rule in EVENT_RULES:
        if rule.pattern.search(text):
            return rule
    return None


def infer_sentiment(text: str, rule: EventRule | None) -> tuple[str, float, float]:
    positive = len(POSITIVE_TERMS.findall(text))
    negative = len(NEGATIVE_TERMS.findall(text))
    if rule and rule.sentiment_label != "neutral":
        score = 0.55 + min(0.35, 0.06 * abs(positive - negative))
        return rule.sentiment_label, score if rule.sentiment_label == "positive" else -score, 0.78
    if positive == negative:
        return "neutral", 0.0, 0.44 if positive else 0.34
    raw = (positive - negative) / max(positive + negative, 1)
    label = "positive" if raw > 0 else "negative"
    return label, max(-1.0, min(1.0, raw)), min(0.82, 0.46 + 0.08 * abs(positive - negative))


def adjust_materiality(base: float, article: NewsArticleForClassification, text: str) -> float:
    score = base
    if article.tickers:
        score += 0.08
    if len(article.tickers) == 1:
        score += 0.04
    if len(text) > 800:
        score += 0.03
    if re.search(r"\b(premarket|after-hours|halt|resumes|breaking)\b", text, re.I):
        score += 0.08
    if not article.tickers:
        score -= 0.18
    return clamp(score)


def fallback_materiality(article: NewsArticleForClassification, text: str) -> float:
    score = 0.18
    if article.tickers:
        score += 0.18
    if len(text) > 800:
        score += 0.08
    if re.search(r"\b(stock|shares|market|nasdaq|nyse|trading)\b", text, re.I):
        score += 0.08
    return clamp(score)


def adjust_urgency(base: float, text: str) -> float:
    score = base
    if re.search(r"\b(premarket|after-hours|halt|resumes|breaking|today|now)\b", text, re.I):
        score += 0.10
    return clamp(score)


def fallback_urgency(text: str) -> float:
    score = 0.22
    if re.search(r"\b(premarket|after-hours|breaking|today|now)\b", text, re.I):
        score += 0.12
    return clamp(score)


def infer_time_horizon(event_type: str, urgency: float, materiality: float) -> str:
    if urgency >= 0.70:
        return "intraday"
    if event_type in {"analyst_rating", "earnings", "capital_markets", "merger_acquisition", "fda_biotech"}:
        return "session_to_multi_day"
    if event_type in {"macro_geopolitical", "crypto"}:
        return "contextual"
    if materiality < 0.35:
        return "contextual"
    return "unknown"


def content_labels(article: NewsArticleForClassification, text: str) -> list[str]:
    labels = []
    if len(article.tickers) == 1:
        labels.append("single_ticker")
    elif len(article.tickers) > 1:
        labels.append("multi_ticker")
    else:
        labels.append("no_direct_ticker")
    if article.channels:
        labels.extend(f"channel:{channel.lower()}" for channel in article.channels[:3])
    if len(text) < 160:
        labels.append("thin_content")
    return labels


def build_summary(article: NewsArticleForClassification) -> str:
    teaser = article.teaser.strip()
    if teaser:
        return teaser[:360]
    text = (article.body_text or article.extracted_text).strip()
    if text:
        sentence = re.split(r"(?<=[.!?])\s+", text)[0]
        return sentence[:360]
    return article.title[:360]


def build_ticker_impacts(
    article: NewsArticleForClassification,
    sentiment_label: str,
    sentiment_score: float,
    confidence: float,
) -> list[TickerImpact]:
    return [
        TickerImpact(
            ticker=ticker.upper(),
            sentiment_label=sentiment_label,
            direction_score=sentiment_score,
            confidence=confidence,
            rationale="direct provider ticker mention",
        )
        for ticker in article.tickers
        if ticker
    ]


def infer_content_completeness(article: NewsArticleForClassification) -> str:
    if article.extracted_text and len(article.extracted_text.strip()) > 300:
        return "pdf_enriched"
    if article.body_text and len(article.body_text.strip()) > 900:
        return "full_text"
    if article.body_text or article.teaser:
        return "short_body"
    return "title_only"


def infer_evidence_basis(article: NewsArticleForClassification) -> str:
    if article.extracted_text and len(article.extracted_text.strip()) > 300:
        return "title_pdf_extract"
    if article.body_text:
        return "title_body"
    if article.teaser:
        return "title_teaser"
    return "title_only"


def build_rationale(rule: EventRule | None, article: NewsArticleForClassification, text: str) -> str:
    if rule:
        return f"Matched {rule.event_type}/{rule.event_subtype}; {len(article.tickers)} direct tickers; text length {len(text)}."
    return f"No high-confidence event rule matched; {len(article.tickers)} direct tickers; text length {len(text)}."


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, round(value, 4)))


def response_to_jsonable(response: IntelligenceResponse) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response.dict()

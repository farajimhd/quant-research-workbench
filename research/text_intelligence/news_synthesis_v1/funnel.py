from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from .engine import NewsSynthesisEngine
from .provider_context import classify_provider_context


FUNNEL_VERSION = "news_synthesis_funnel_v4"
FINAL_LANES = frozenset(("forecast_event", "context_only", "insufficient_information"))
FORECAST_LABELS = frozenset(("eligible", "ineligible", "insufficient_information"))

_POSITIVE_CONTEXT_RE = re.compile(
    r"\b(?:upgrade[sd]?|raises? (?:the )?price target|buy|outperform|overweight|"
    r"bullish|gainers?|trading higher|shares? (?:rise|gain|jump|surge))\b",
    re.I,
)
_NEGATIVE_CONTEXT_RE = re.compile(
    r"\b(?:downgrade[sd]?|cuts? (?:the )?price target|sell|underperform|underweight|"
    r"bearish|losers?|trading lower|shares? (?:fall|drop|slide|sink))\b",
    re.I,
)


def _context_sentiment(source: Mapping[str, Any]) -> str:
    title = str(source.get("title") or "")
    positive = bool(_POSITIVE_CONTEXT_RE.search(title))
    negative = bool(_NEGATIVE_CONTEXT_RE.search(title))
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "neutral"


def _source_fields(source: Mapping[str, Any]) -> tuple[str, str, str]:
    source_id = str(source.get("source_id") or source.get("canonical_news_id") or "").strip()
    timestamp = str(source.get("source_timestamp") or source.get("published_at_utc") or "").strip()
    title = str(source.get("title") or "").strip()
    body = str(source.get("text") or source.get("rendered_text") or "").strip()
    text = body or title
    return source_id, timestamp, text


class NewsSynthesisFunnel:
    """Execute the cheap provider gate and only pay for semantic synthesis when required."""

    def __init__(self, engine: NewsSynthesisEngine) -> None:
        self.engine = engine

    def process(self, source: Mapping[str, Any]) -> dict[str, Any]:
        source_id, timestamp, text = _source_fields(source)
        if not source_id or not timestamp or not text:
            return self._insufficient(source_id, timestamp, text)
        prefilter = classify_provider_context(source)
        if prefilter["route"] == "context_only":
            result = self._fast_context(source, source_id, timestamp, text, prefilter)
        else:
            synthesis = self.engine.synthesize(source)
            result = self._semantic_result(source_id, timestamp, text, prefilter, synthesis)
        validate_funnel_result(result)
        return result

    @staticmethod
    def _insufficient(source_id: str, timestamp: str, text: str) -> dict[str, Any]:
        result = {
            "funnel_version": FUNNEL_VERSION,
            "source_id": source_id,
            "source_timestamp": timestamp,
            "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "prefilter": None,
            "final": {
                "lane": "insufficient_information",
                "forecast_eligibility": "insufficient_information",
                "context_class": "insufficient_information",
                "analysis_depth": "none",
                "context_preserved": True,
                "reason_codes": ["missing_required_source_field"],
            },
            "ticker_labels": [],
            "synthesis_document": None,
        }
        validate_funnel_result(result)
        return result

    @staticmethod
    def _fast_context(
        source: Mapping[str, Any],
        source_id: str,
        timestamp: str,
        text: str,
        prefilter: Mapping[str, Any],
    ) -> dict[str, Any]:
        tickers = sorted({str(value).strip().upper() for value in source.get("tickers") or source.get("entity_terms") or () if str(value).strip()})
        sentiment = _context_sentiment(source)
        return {
            "funnel_version": FUNNEL_VERSION,
            "source_id": source_id,
            "source_timestamp": timestamp,
            "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "prefilter": dict(prefilter),
            "final": {
                "lane": "context_only",
                "forecast_eligibility": "ineligible",
                "context_class": str(prefilter["content_family"]),
                "analysis_depth": "fast_context",
                "context_preserved": True,
                "reason_codes": ["certified_prefilter_context_family"],
            },
            "ticker_labels": [
                {
                    "ticker": ticker,
                    "identity_status": "provider_reported_unresolved",
                    "forecast_eligibility": "ineligible",
                    "analyst_evaluation": str(prefilter["content_family"]) in {
                        "analyst_rating_roundup", "analyst_forecast_roundup", "direct_analyst_action"
                    },
                    "sentiment": sentiment,
                    "sentiment_scope": "headline_context",
                }
                for ticker in tickers
            ],
            "synthesis_document": None,
        }

    @staticmethod
    def _semantic_result(
        source_id: str,
        timestamp: str,
        text: str,
        prefilter: Mapping[str, Any],
        synthesis: Mapping[str, Any],
    ) -> dict[str, Any]:
        entities = {str(row["entity_id"]): row for row in synthesis["entities"]}
        views = {str(row["entity_id"]): row for row in synthesis["issuer_views"]}
        eligibility = {
            (str(row["entity_id"]), str(row["product"])): bool(row["eligible"])
            for row in synthesis["eligibility"]
        }
        ticker_labels = []
        for entity_id, entity in entities.items():
            if entity.get("entity_kind") not in {"issuer", "security"}:
                continue
            ticker = str(entity.get("ticker") or "")
            if not ticker:
                continue
            forecast = eligibility.get((entity_id, "forecast_trigger"), False)
            view = views.get(entity_id, {})
            ticker_labels.append({
                "ticker": ticker,
                "identity_status": str(entity.get("identity_status") or "unresolved"),
                "forecast_eligibility": "eligible" if forecast else "ineligible",
                "analyst_evaluation": eligibility.get((entity_id, "analyst_evaluation"), False),
                "sentiment": str(view.get("composite_sentiment") or "neutral"),
                "sentiment_scope": "full_semantic",
            })
        article_eligible = any(row["forecast_eligibility"] == "eligible" for row in ticker_labels)
        analyst_context = any(bool(row["analyst_evaluation"]) for row in ticker_labels)
        context_class = (
            "analyst_opinion" if analyst_context
            else str(prefilter["content_family"]) if prefilter["content_family"] != "unclassified"
            else "other_context"
        )
        return {
            "funnel_version": FUNNEL_VERSION,
            "source_id": source_id,
            "source_timestamp": timestamp,
            "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "prefilter": dict(prefilter),
            "final": {
                "lane": "forecast_event" if article_eligible else "context_only",
                "forecast_eligibility": "eligible" if article_eligible else "ineligible",
                "context_class": "not_applicable" if article_eligible else context_class,
                "analysis_depth": "full_semantic",
                "context_preserved": not article_eligible,
                "reason_codes": [
                    "semantic_forecast_event" if article_eligible else "semantic_no_forecast_event"
                ],
            },
            "ticker_labels": ticker_labels,
            "synthesis_document": dict(synthesis),
        }


def validate_funnel_result(result: Mapping[str, Any]) -> None:
    if result.get("funnel_version") != FUNNEL_VERSION:
        raise ValueError("invalid funnel version")
    if len(str(result.get("source_text_sha256") or "")) != 64:
        raise ValueError("invalid funnel source hash")
    final = result.get("final")
    if not isinstance(final, Mapping):
        raise ValueError("missing final funnel decision")
    if final.get("lane") not in FINAL_LANES:
        raise ValueError("invalid final funnel lane")
    if final.get("forecast_eligibility") not in FORECAST_LABELS:
        raise ValueError("invalid final forecast label")
    if not isinstance(final.get("context_preserved"), bool):
        raise ValueError("invalid context preservation flag")
    if not isinstance(result.get("ticker_labels"), list):
        raise ValueError("invalid ticker labels")
    ticker_labels = result["ticker_labels"]
    for row in ticker_labels:
        if not isinstance(row, Mapping) or not str(row.get("ticker") or ""):
            raise ValueError("invalid ticker identity")
        if row.get("forecast_eligibility") not in FORECAST_LABELS:
            raise ValueError("invalid ticker forecast label")
        if row.get("sentiment") not in {"positive", "negative", "mixed", "neutral", "insufficient_information"}:
            raise ValueError("invalid ticker sentiment")
        if not isinstance(row.get("analyst_evaluation"), bool):
            raise ValueError("invalid analyst-evaluation label")
    any_eligible = any(row["forecast_eligibility"] == "eligible" for row in ticker_labels)
    if final["lane"] == "forecast_event" and (final["forecast_eligibility"] != "eligible" or not any_eligible):
        raise ValueError("forecast-event lane lacks eligible ticker")
    if final["lane"] == "context_only" and final["forecast_eligibility"] != "ineligible":
        raise ValueError("context-only lane must be forecast-ineligible")
    if final["analysis_depth"] == "fast_context" and result.get("synthesis_document") is not None:
        raise ValueError("fast-context result must not contain semantic synthesis")
    if final["analysis_depth"] == "full_semantic" and result.get("synthesis_document") is None:
        raise ValueError("full-semantic result lacks synthesis document")

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import IntelligenceConfig
from .heuristics import heuristic_event_type, heuristic_materiality, heuristic_sentiment, heuristic_urgency, ticker_impacts
from .model_registry import ModelRegistry
from .schemas import IntelligenceResponse, NewsArticleForClassification, TickerImpact


class FastClassifierTier:
    def __init__(self, config: IntelligenceConfig, registry: ModelRegistry) -> None:
        self.config = config
        self.registry = registry
        self._pipeline: Any | None = None
        self._load_error = ""

    @property
    def load_error(self) -> str:
        return self._load_error

    def classify(self, article: NewsArticleForClassification, text: str) -> dict[str, Any]:
        if self.config.enable_models:
            loaded = self._load_pipeline()
            if loaded is not None:
                try:
                    output = loaded(text[:512], truncation=True)
                    item = output[0] if isinstance(output, list) and output else output
                    label = str(item.get("label", "neutral")).lower()
                    confidence = float(item.get("score", 0.0))
                    normalized = normalize_sentiment_label(label)
                    score = sentiment_direction(normalized, confidence)
                    return {
                        "model": self.config.active_sentiment_model,
                        "label": normalized,
                        "score": score,
                        "confidence": confidence,
                        "raw": item,
                    }
                except Exception as error:  # pragma: no cover - defensive runtime path
                    self._load_error = f"inference_failed: {error}"
        label, score, confidence = heuristic_sentiment(text)
        return {
            "model": "heuristic-sentiment",
            "label": label,
            "score": score,
            "confidence": confidence,
            "raw": {"error": self._load_error},
        }

    def _load_pipeline(self) -> Any | None:
        if self._pipeline is not None:
            return self._pipeline
        local_path = self.registry.path_for(self.config.active_sentiment_model)
        if not self.registry.exists(self.config.active_sentiment_model):
            self._load_error = f"model_not_downloaded: {local_path}"
            return None
        try:
            from transformers import pipeline

            self._pipeline = pipeline("text-classification", model=str(local_path), tokenizer=str(local_path))
        except Exception as error:  # pragma: no cover - depends on optional packages/models
            self._load_error = f"load_failed: {error}"
            self._pipeline = None
        return self._pipeline


class EntityEventTier:
    def __init__(self, config: IntelligenceConfig, registry: ModelRegistry) -> None:
        self.config = config
        self.registry = registry

    def classify(self, article: NewsArticleForClassification, text: str) -> dict[str, Any]:
        event_type = heuristic_event_type(text)
        materiality = heuristic_materiality(article, text, event_type)
        urgency = heuristic_urgency(text)
        labels = sorted(set(article.catalyst_labels + [event_type, article.content_scope, article.scanner_relevance]))
        return {
            "model": "heuristic-event-v1",
            "event_type": event_type,
            "event_subtype": "",
            "materiality_score": materiality,
            "novelty_score": 0.0,
            "urgency_score": urgency,
            "time_horizon": time_horizon(event_type, urgency),
            "labels": [label for label in labels if label],
            "raw": {},
        }


class LlmTier:
    def __init__(self, config: IntelligenceConfig) -> None:
        self.config = config

    def should_run(self, article: NewsArticleForClassification, text: str, materiality: float) -> bool:
        return (
            self.config.enable_llm
            and len(text.strip()) >= self.config.llm_min_text_chars
            and materiality >= self.config.llm_min_materiality
            and bool(article.tickers or article.insight_tickers)
        )

    def classify(self, article: NewsArticleForClassification, text: str, current: IntelligenceResponse) -> dict[str, Any]:
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You classify financial news for a trading scanner. "
                        "Return strict JSON only with keys: event_type, event_subtype, "
                        "materiality_score, novelty_score, urgency_score, time_horizon, "
                        "affected_tickers, labels, rationale."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "title": article.title,
                            "tickers": article.tickers or article.insight_tickers,
                            "publisher": article.publisher_name,
                            "text": text[: self.config.max_text_chars],
                            "current_labels": response_to_dict(current),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 600,
        }
        request = urllib.request.Request(
            f"{self.config.llm_base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.llm_timeout_ms / 1000) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(extract_json(content))
            return {"model": self.config.llm_model, "parsed": parsed, "raw": data}
        except (OSError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as error:
            return {"model": self.config.llm_model, "error": str(error), "raw": {}}


class IntelligenceEngine:
    def __init__(self, config: IntelligenceConfig) -> None:
        self.config = config
        self.registry = ModelRegistry(config)
        self.fast = FastClassifierTier(config, self.registry)
        self.entity = EntityEventTier(config, self.registry)
        self.llm = LlmTier(config)

    def classify(self, article: NewsArticleForClassification) -> IntelligenceResponse:
        text = article.classification_text(self.config.max_text_chars)
        sentiment = self.fast.classify(article, text)
        event = self.entity.classify(article, text)
        response = IntelligenceResponse(
            stack_version=self.config.stack_version,
            taxonomy_version=self.config.taxonomy_version,
            prompt_version=self.config.prompt_version,
            model_stack=[sentiment["model"], event["model"]],
            sentiment_label=sentiment["label"],
            sentiment_score=sentiment["score"],
            sentiment_confidence=sentiment["confidence"],
            event_type=event["event_type"],
            event_subtype=event["event_subtype"],
            materiality_score=event["materiality_score"],
            novelty_score=event["novelty_score"],
            urgency_score=event["urgency_score"],
            time_horizon=event["time_horizon"],
            affected_tickers=ticker_impacts(article, sentiment["label"], sentiment["score"], sentiment["confidence"]),
            labels=event["labels"],
            rationale="fast classifier plus deterministic event rules",
            raw_outputs={"sentiment": sentiment["raw"], "event": event["raw"]},
        )
        if self.llm.should_run(article, text, response.materiality_score):
            llm_output = self.llm.classify(article, text, response)
            response.model_stack.append(llm_output["model"])
            response.raw_outputs["llm"] = llm_output.get("raw", {})
            if "error" in llm_output:
                response.error = f"llm_failed: {llm_output['error']}"
                return response
            apply_llm_output(response, llm_output.get("parsed", {}))
        return response


def normalize_sentiment_label(label: str) -> str:
    lower = label.lower()
    if "positive" in lower or lower in {"bullish", "pos", "label_2"}:
        return "positive"
    if "negative" in lower or lower in {"bearish", "neg", "label_0"}:
        return "negative"
    return "neutral"


def sentiment_direction(label: str, confidence: float) -> float:
    if label == "positive":
        return confidence
    if label == "negative":
        return -confidence
    return 0.0


def time_horizon(event_type: str, urgency: float) -> str:
    if urgency >= 0.65:
        return "intraday"
    if event_type in {"earnings", "analyst_rating", "capital_markets", "fda_biotech"}:
        return "session_to_multi_day"
    if event_type in {"macro_geopolitical", "crypto"}:
        return "contextual"
    return "unknown"


def extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def apply_llm_output(response: IntelligenceResponse, parsed: dict[str, Any]) -> None:
    response.event_type = str(parsed.get("event_type") or response.event_type)
    response.event_subtype = str(parsed.get("event_subtype") or response.event_subtype)
    response.materiality_score = bounded_float(parsed.get("materiality_score"), response.materiality_score)
    response.novelty_score = bounded_float(parsed.get("novelty_score"), response.novelty_score)
    response.urgency_score = bounded_float(parsed.get("urgency_score"), response.urgency_score)
    response.time_horizon = str(parsed.get("time_horizon") or response.time_horizon)
    response.labels = sorted(set(response.labels + [str(item) for item in parsed.get("labels", []) if item]))
    response.rationale = str(parsed.get("rationale") or response.rationale)
    impacts = []
    for item in parsed.get("affected_tickers", []):
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        impacts.append(
            TickerImpact(
                ticker=str(item["ticker"]).upper(),
                sentiment_label=str(item.get("sentiment_label") or response.sentiment_label),
                direction_score=bounded_float(item.get("direction_score"), response.sentiment_score, -1.0, 1.0),
                confidence=bounded_float(item.get("confidence"), response.sentiment_confidence),
                rationale=str(item.get("rationale") or ""),
            )
        )
    if impacts:
        response.affected_tickers = impacts


def bounded_float(value: Any, default: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def response_to_dict(response: IntelligenceResponse) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response.dict()

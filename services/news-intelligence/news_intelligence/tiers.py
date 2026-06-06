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
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(str(local_path))
            model = AutoModelForSequenceClassification.from_pretrained(str(local_path))
            device = resolve_torch_device(torch, self.config.model_device)
            model.to(device)
            model.eval()
            id2label = getattr(model.config, "id2label", {}) or {}

            def classify(text: str, truncation: bool = True) -> list[dict[str, Any]]:
                encoded = tokenizer(
                    text,
                    truncation=truncation,
                    max_length=512,
                    return_tensors="pt",
                    return_token_type_ids=False,
                )
                encoded = {key: value.to(device) for key, value in encoded.items()}
                with torch.no_grad():
                    logits = model(**encoded).logits[0]
                    probabilities = torch.softmax(logits, dim=-1)
                index = int(torch.argmax(probabilities).item())
                label = str(id2label.get(index, f"label_{index}"))
                return [{"label": label, "score": float(probabilities[index].item())}]

            self._pipeline = classify
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
                        f"{reasoning_instruction(self.config)}"
                        "You classify financial news for a trading scanner. "
                        "Return only the final JSON object. No markdown. "
                        "Use this exact contract: summary string; event_type string; event_subtype string; "
                        "materiality_score number 0 to 1; novelty_score number 0 to 1; urgency_score number 0 to 1; "
                        "time_horizon one of intraday, session_to_multi_day, longer_term, contextual, unknown; "
                        "affected_tickers array of objects with ticker, sentiment_label, direction_score, confidence, rationale; "
                        "content_completeness string; evidence_basis string; labels array of strings; rationale string. "
                        "Do not invent tickers not provided in the user payload."
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
            "max_tokens": self.config.llm_max_tokens,
        }
        if self.config.llm_reasoning_effort:
            payload["reasoning_effort"] = self.config.llm_reasoning_effort
        if self.config.llm_response_format:
            payload["response_format"] = {"type": self.config.llm_response_format}
        request = urllib.request.Request(
            f"{self.config.llm_base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.llm_timeout_ms / 1000) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content")
            if not content:
                return {"model": self.config.llm_model, "error": "empty_llm_content", "raw": data}
            parsed = json.loads(extract_json(content))
            return {"model": self.config.llm_model, "parsed": parsed, "raw": data}
        except (OSError, urllib.error.HTTPError, KeyError, TypeError, json.JSONDecodeError) as error:
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
            summary=article.title[:280],
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
            content_completeness=infer_content_completeness(article),
            evidence_basis=infer_evidence_basis(article),
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
            apply_llm_output(response, llm_output.get("parsed", {}), self.config.llm_merge_mode)
        return response


def normalize_sentiment_label(label: str) -> str:
    lower = label.lower()
    if "positive" in lower or lower in {"bullish", "pos", "label_2"}:
        return "positive"
    if "negative" in lower or lower in {"bearish", "neg", "label_0"}:
        return "negative"
    return "neutral"


def reasoning_instruction(config: IntelligenceConfig) -> str:
    if not config.llm_reasoning_effort:
        return ""
    return f"Reasoning effort: {config.llm_reasoning_effort}. "


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
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return json.dumps(value)
    return "{}"


def apply_llm_output(response: IntelligenceResponse, parsed: dict[str, Any], merge_mode: str = "summary_only") -> None:
    response.summary = str(parsed.get("summary") or response.summary)
    response.rationale = str(parsed.get("rationale") or response.rationale)
    response.novelty_score = bounded_float(parsed.get("novelty_score"), response.novelty_score)
    if merge_mode != "override":
        return
    response.event_type = str(parsed.get("event_type") or response.event_type)
    response.event_subtype = str(parsed.get("event_subtype") or response.event_subtype)
    response.materiality_score = bounded_float(parsed.get("materiality_score"), response.materiality_score)
    response.urgency_score = bounded_float(parsed.get("urgency_score"), response.urgency_score)
    response.time_horizon = str(parsed.get("time_horizon") or response.time_horizon)
    response.content_completeness = str(parsed.get("content_completeness") or response.content_completeness)
    response.evidence_basis = str(parsed.get("evidence_basis") or response.evidence_basis)
    response.labels = sorted(set(response.labels + [str(item) for item in parsed.get("labels", []) if item]))
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


def resolve_torch_device(torch_module: Any, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    return "cuda" if torch_module.cuda.is_available() else "cpu"


def response_to_dict(response: IntelligenceResponse) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response.dict()


def infer_content_completeness(article: NewsArticleForClassification) -> str:
    if article.extracted_text and len(article.extracted_text.strip()) > 300:
        return "url_enriched"
    if article.body_text and len(article.body_text.strip()) > 300:
        return "full_text"
    if article.body_text or article.teaser:
        return "short_body"
    return "title_only"


def infer_evidence_basis(article: NewsArticleForClassification) -> str:
    if article.extracted_text and len(article.extracted_text.strip()) > 300:
        return "title_url_extract"
    if article.body_text:
        return "title_body"
    if article.teaser:
        return "title_teaser"
    if article.insight_tickers or article.insight_sentiments:
        return "provider_insights"
    return "title_only"

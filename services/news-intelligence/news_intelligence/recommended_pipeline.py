from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

from .config import IntelligenceConfig
from .model_registry import ModelRegistry
from .schemas import IntelligenceResponse, NewsArticleForClassification
from .supervision import (
    adjust_materiality,
    adjust_urgency,
    build_summary,
    build_ticker_impacts,
    clamp,
    content_labels,
    fallback_materiality,
    fallback_urgency,
    infer_content_completeness,
    infer_evidence_basis,
    infer_sentiment,
    infer_time_horizon,
    match_event_rule,
    response_to_jsonable,
)
from .tiers import FastClassifierTier, LlmTier, apply_llm_output


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str
    seconds: float
    output: dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class PipelineRun:
    response: IntelligenceResponse
    stages: list[StageResult]
    total_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": response_to_jsonable(self.response),
            "stages": [stage.__dict__ for stage in self.stages],
            "total_seconds": self.total_seconds,
        }


class RecommendedNewsPipeline:
    """Runtime pipeline proposed for news-gateway classification calls."""

    def __init__(
        self,
        config: IntelligenceConfig | None = None,
        sentiment_model_key: str | None = None,
        enable_models: bool | None = None,
        enable_llm: bool | None = None,
    ) -> None:
        base_config = config or IntelligenceConfig.from_env()
        updates: dict[str, Any] = {}
        if sentiment_model_key:
            updates["active_sentiment_model"] = sentiment_model_key
        if enable_models is not None:
            updates["enable_models"] = enable_models
        if enable_llm is not None:
            updates["enable_llm"] = enable_llm
        self.config = replace(base_config, **updates) if updates else base_config
        self.registry = ModelRegistry(self.config)
        self.fast = FastClassifierTier(self.config, self.registry)
        self.llm = LlmTier(self.config)

    def classify(self, article: NewsArticleForClassification) -> PipelineRun:
        started = time.perf_counter()
        stages: list[StageResult] = []

        text, preprocess = timed_stage("preprocess", lambda: self._preprocess(article))
        stages.append(preprocess)

        sentiment, sentiment_stage = timed_stage("fast_sentiment", lambda: self._fast_sentiment(article, text))
        stages.append(sentiment_stage)

        event, event_stage = timed_stage("event_rules", lambda: self._event_rules(article, text, sentiment))
        stages.append(event_stage)

        response, post_stage = timed_stage("postprocess", lambda: self._build_response(article, sentiment, event))
        stages.append(post_stage)

        llm_stage = self._maybe_apply_llm(article, text, response)
        if llm_stage is not None:
            stages.append(llm_stage)

        response.raw_outputs["stage_timings"] = {stage.name: stage.seconds for stage in stages}
        total = time.perf_counter() - started
        return PipelineRun(response=response, stages=stages, total_seconds=total)

    def _preprocess(self, article: NewsArticleForClassification) -> str:
        return article.classification_text(self.config.max_text_chars)

    def _fast_sentiment(self, article: NewsArticleForClassification, text: str) -> dict[str, Any]:
        return self.fast.classify(article, text)

    def _event_rules(
        self,
        article: NewsArticleForClassification,
        text: str,
        sentiment: dict[str, Any],
    ) -> dict[str, Any]:
        rule = match_event_rule(text)
        if rule:
            materiality = adjust_materiality(rule.base_materiality, article, text)
            urgency = adjust_urgency(rule.base_urgency, text)
            labels = sorted(set(list(rule.labels) + content_labels(article, text)))
            return {
                "model": "recommended-event-rules-v1",
                "event_type": rule.event_type,
                "event_subtype": rule.event_subtype,
                "materiality_score": materiality,
                "urgency_score": urgency,
                "time_horizon": infer_time_horizon(rule.event_type, urgency, materiality),
                "labels": labels,
                "matched_rule": rule.event_subtype,
            }
        supervisor_sentiment = infer_sentiment(text, None)
        materiality = fallback_materiality(article, text)
        urgency = fallback_urgency(text)
        return {
            "model": "recommended-event-rules-v1",
            "event_type": "other",
            "event_subtype": "",
            "materiality_score": materiality,
            "urgency_score": urgency,
            "time_horizon": infer_time_horizon("other", urgency, materiality),
            "labels": sorted(set(["other"] + content_labels(article, text))),
            "matched_rule": "",
            "fallback_sentiment": supervisor_sentiment,
            "sentiment_model_label": sentiment.get("label"),
        }

    def _build_response(
        self,
        article: NewsArticleForClassification,
        sentiment: dict[str, Any],
        event: dict[str, Any],
    ) -> IntelligenceResponse:
        label = str(sentiment.get("label") or "neutral")
        score = float(sentiment.get("score") or 0.0)
        confidence = float(sentiment.get("confidence") or 0.0)
        response = IntelligenceResponse(
            stack_version=self.config.stack_version,
            taxonomy_version=self.config.taxonomy_version,
            prompt_version=self.config.prompt_version,
            model_stack=[str(sentiment.get("model") or "sentiment"), str(event.get("model") or "event")],
            summary=build_summary(article),
            sentiment_label=label,
            sentiment_score=clamp(score, -1.0, 1.0),
            sentiment_confidence=clamp(confidence),
            event_type=str(event.get("event_type") or "other"),
            event_subtype=str(event.get("event_subtype") or ""),
            materiality_score=clamp(float(event.get("materiality_score") or 0.0)),
            novelty_score=0.0,
            urgency_score=clamp(float(event.get("urgency_score") or 0.0)),
            time_horizon=str(event.get("time_horizon") or "unknown"),
            affected_tickers=build_ticker_impacts(article, label, clamp(score, -1.0, 1.0), clamp(confidence)),
            content_completeness=infer_content_completeness(article),
            evidence_basis=infer_evidence_basis(article),
            labels=list(event.get("labels") or []),
            rationale="recommended pipeline: normalize, fast sentiment, event rules, optional LLM, postprocess",
            raw_outputs={"sentiment": sentiment.get("raw", {}), "event": event},
        )
        return response

    def _maybe_apply_llm(
        self,
        article: NewsArticleForClassification,
        text: str,
        response: IntelligenceResponse,
    ) -> StageResult | None:
        if not self.llm.should_run(article, text, response.materiality_score):
            return StageResult("llm", "skipped", 0.0, {"reason": "threshold_or_disabled"})
        started = time.perf_counter()
        output = self.llm.classify(article, text, response)
        seconds = time.perf_counter() - started
        response.model_stack.append(output["model"])
        response.raw_outputs["llm"] = output.get("raw", {})
        if "error" in output:
            response.error = f"llm_failed: {output['error']}"
            return StageResult("llm", "failed", seconds, {"model": output["model"]}, output["error"])
        apply_llm_output(response, output.get("parsed", {}), self.config.llm_merge_mode)
        return StageResult("llm", "completed", seconds, {"model": output["model"]})


def timed_stage(name: str, callback: Any) -> tuple[Any, StageResult]:
    started = time.perf_counter()
    try:
        output = callback()
    except Exception as error:
        seconds = time.perf_counter() - started
        return None, StageResult(name=name, status="failed", seconds=seconds, output={}, error=str(error))
    seconds = time.perf_counter() - started
    return output, StageResult(name=name, status="completed", seconds=seconds, output=summarize_stage_output(output))


def summarize_stage_output(output: Any) -> dict[str, Any]:
    if isinstance(output, str):
        return {"text_chars": len(output)}
    if isinstance(output, dict):
        return {key: value for key, value in output.items() if key != "raw"}
    if isinstance(output, IntelligenceResponse):
        return {
            "event_type": output.event_type,
            "sentiment_label": output.sentiment_label,
            "materiality_score": output.materiality_score,
            "urgency_score": output.urgency_score,
        }
    return {"type": type(output).__name__}

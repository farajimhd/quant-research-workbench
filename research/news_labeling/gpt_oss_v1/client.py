from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from .config import LabelingConfig
from .prompt import build_messages
from .schema import VLLM_TRANSPORT_SCHEMA, validate_label


class LocalModelError(RuntimeError):
    pass


class LocalModelHttpError(LocalModelError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.retryable = status_code in {408, 409, 425, 429} or status_code >= 500
        super().__init__(f"Model HTTP {status_code}: {body[:1_000]}")


def check_server(config: LabelingConfig) -> None:
    models_url = config.endpoint.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        with request.urlopen(models_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise LocalModelError(
            f"Local model server is unavailable at {models_url}. "
            f"Start the requested model {config.model!r} before executing labels."
        ) from exc
    identifiers = {str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict)}
    if identifiers and config.model not in identifiers:
        raise LocalModelError(f"Requested model {config.model!r} is not served; available={sorted(identifiers)}")


def build_request_payload(article: dict[str, Any], config: LabelingConfig) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": build_messages(article),
        "temperature": 0,
        "max_tokens": config.max_output_tokens,
        "reasoning_effort": "low",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "news_semantic_label",
                "strict": True,
                "schema": VLLM_TRANSPORT_SCHEMA,
            },
        },
    }


def label_article(article: dict[str, Any], config: LabelingConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = build_request_payload(article, config)
    last_error: Exception | None = None
    attempts_made = 0
    request_started = time.perf_counter()
    for attempt in range(1, config.attempts + 1):
        attempts_made = attempt
        try:
            attempt_started = time.perf_counter()
            response = _post_json(config.endpoint, payload, config.timeout_seconds)
            attempt_seconds = time.perf_counter() - attempt_started
            content = response["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text") or "") for item in content if isinstance(item, dict)
                )
            label = json.loads(str(content))
            supplied_text = "\n".join((
                str(article.get("title") or ""),
                str(article.get("rendered_text") or ""),
            ))
            errors = validate_label(label, supplied_text)
            if errors:
                raise LocalModelError("; ".join(errors))
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            completion_tokens = int(usage.get("completion_tokens") or 0)
            return label, {
                "attempt": attempt,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": completion_tokens,
                "total_tokens": int(usage.get("total_tokens") or 0),
                "attempt_seconds": round(attempt_seconds, 6),
                "total_seconds": round(time.perf_counter() - request_started, 6),
                "completion_tokens_per_second": round(
                    completion_tokens / attempt_seconds if attempt_seconds else 0.0,
                    6,
                ),
            }
        except (KeyError, TypeError, ValueError, OSError, LocalModelError) as exc:
            last_error = exc
            if isinstance(exc, LocalModelHttpError) and not exc.retryable:
                break
            if attempt < config.attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise LocalModelError(f"Label failed after {attempts_made} attempts: {last_error}") from last_error


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LocalModelHttpError(exc.code, body) from exc

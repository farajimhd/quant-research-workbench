from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from .config import LabelingConfig
from .prompt import build_messages
from .schema import TRANSPORT_SCHEMA, validate_label


class LocalModelError(RuntimeError):
    pass


def check_server(config: LabelingConfig) -> None:
    models_url = config.endpoint.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        with request.urlopen(models_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise LocalModelError(
            f"Local model server is unavailable at {models_url}. Start gpt-oss-20b before executing labels."
        ) from exc
    identifiers = {str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict)}
    if identifiers and config.model not in identifiers:
        raise LocalModelError(f"Requested model {config.model!r} is not served; available={sorted(identifiers)}")


def label_article(article: dict[str, Any], config: LabelingConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
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
                "schema": TRANSPORT_SCHEMA,
            },
        },
    }
    last_error: Exception | None = None
    for attempt in range(1, config.attempts + 1):
        try:
            response = _post_json(config.endpoint, payload, config.timeout_seconds)
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
            return label, {
                "attempt": attempt,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            }
        except (KeyError, TypeError, ValueError, OSError, LocalModelError) as exc:
            last_error = exc
            if attempt < config.attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise LocalModelError(f"Label failed after {config.attempts} attempts: {last_error}") from last_error


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LocalModelError(f"Model HTTP {exc.code}: {body[:1_000]}") from exc

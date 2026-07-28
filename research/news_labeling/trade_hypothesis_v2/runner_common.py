from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .contract import (
    CONTRACT_VERSION,
    HYPOTHESIS_SCHEMA,
    PROMPT_VERSION,
    build_messages,
    validate_hypothesis,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def context_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    if len(rows) != 90:
        raise RuntimeError(f"Expected exactly 90 frozen single-ticker contexts, got {len(rows)}")
    identities = {
        (
            str(row["canonical_news_id"]),
            str(row["ticker"]),
            str(row["published_at_utc"]),
        )
        for row in rows
    }
    if len(identities) != 90:
        raise RuntimeError("Frozen context identities are not unique")
    return rows


def chat_body(
    row: dict[str, Any],
    *,
    model: str,
    max_output_tokens: int,
    reasoning_effort: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": build_messages(row["context"]),
        "temperature": 0,
        "max_completion_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "news_trade_hypothesis",
                "strict": True,
                "schema": HYPOTHESIS_SCHEMA,
            },
        },
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    return body


def parse_chat_response(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        )
    result = json.loads(str(content))
    validate_hypothesis(result)
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return result, {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def result_row(
    source: dict[str, Any],
    *,
    model: str,
    provider: str,
    prediction: dict[str, Any],
    usage: dict[str, int],
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "canonical_news_id": source["canonical_news_id"],
        "ticker": source["ticker"],
        "published_at_utc": source["published_at_utc"],
        "model": model,
        "provider": provider,
        "status": "completed",
        "prediction": prediction,
        "targets": source.get("targets") or {},
        "usage": usage,
    }
    if elapsed_seconds is not None:
        value["elapsed_seconds"] = round(elapsed_seconds, 6)
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

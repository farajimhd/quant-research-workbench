from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


MODEL_TEXT_CONTRACT_VERSION = "benzinga_title_plus_canonical_body_v1"
BODY_TEXT_CONTRACT_VERSION = "benzinga_canonical_body_only_v1"


@dataclass(frozen=True, slots=True)
class NewsModelText:
    """Versioned model input derived from explicit title and canonical body fields."""

    title: str
    body: str
    text: str
    text_hash: str
    body_status: str
    text_contract: str = MODEL_TEXT_CONTRACT_VERSION


def compose_model_text(title: Any, canonical_body: Any) -> str:
    """Keep headline signal while preserving the canonical body as a separate authority."""

    clean_title = str(title or "").strip()
    clean_body = str(canonical_body or "").strip()
    if clean_title and clean_body:
        return f"{clean_title}\n\n{clean_body}"
    return clean_title or clean_body


def model_text_from_body_v3(row: Mapping[str, Any]) -> NewsModelText:
    title = str(row.get("title") or "").strip()
    body = str(row.get("canonical_body_text") or row.get("body") or "").strip()
    text = compose_model_text(title, body)
    return NewsModelText(
        title=title,
        body=body,
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        body_status=str(row.get("body_status") or ("complete" if body else "missing")),
    )


def body_v3_source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt a joined Body V3 row without presenting title fallback as article body."""

    model_text = model_text_from_body_v3(row)
    result = dict(row)
    result.update({
        "title": model_text.title,
        "canonical_body_text": model_text.body,
        "text": model_text.text,
        "model_text_hash": model_text.text_hash,
        "model_text_contract": model_text.text_contract,
        "body_status": model_text.body_status,
        "body_text_contract": str(row.get("text_contract") or BODY_TEXT_CONTRACT_VERSION),
    })
    return result

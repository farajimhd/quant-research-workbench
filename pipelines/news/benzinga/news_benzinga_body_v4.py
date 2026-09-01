from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

from pipelines.news.benzinga.news_benzinga_body_v3 import (
    BODY_SOURCE_SELECTION_VERSION,
    BodyBlock,
    BodyContract,
    CanonicalBodyArticle,
    _CONTROL,
    _DISCLOSURE,
    _IMAGE_JUNK,
    _NAV,
    _NAV_WORD,
    _PROMO,
    _SOURCE_WRAPPER,
    _clean,
    _looks_like_link_title,
    _sha256,
    build_body_rows,
    render_canonical_body as render_body_with_contract,
)
from pipelines.news.benzinga.news_benzinga_render_v2 import NewsBlock, RenderedNewsArticle


BODY_CLEANER_VERSION = "benzinga_body_cleaner_v4"
BODY_RENDERER_VERSION = "benzinga_body_renderer_v4"
BODY_TEXT_CONTRACT = "benzinga_canonical_body_only_v2"
BODY_V4_CONTRACT = BodyContract(
    source_selection_version=BODY_SOURCE_SELECTION_VERSION,
    cleaner_version=BODY_CLEANER_VERSION,
    renderer_version=BODY_RENDERER_VERSION,
    text_contract=BODY_TEXT_CONTRACT,
)

# Classification uses a structural view so Markdown/HTML list bullets do not
# hide publisher chrome. Stored original and cleaned text remain auditable.
_STRUCTURAL_PREFIX = re.compile(r"^\s*(?:(?:[-*+•‣▪–—]|\d{1,3}[.)])\s+)+")
_RELATED_MARKER = re.compile(
    r"^(?:read|see|watch|listen)(?:\s+(?:also|next|more|related))?\s*[,:;.!?\"'’”»›-]*\s*$",
    re.IGNORECASE,
)
_RELATED_PREFIX = re.compile(
    r"^(?:(?:read|see|watch|listen)\s+(?:also|next|more|related)\b\s*(?::|>|-|,|;|\.|\"|”|\s)\s*.+|"
    r"read\s+full\s+article\b.*|continue\s+reading\b.*|"
    r"(?:to|for)\s+(?:read|see|watch|learn|find\s+out)\s+more\b.*)$",
    re.IGNORECASE,
)
_BARE_RELATED_CTA = re.compile(
    r"^(?:read|see|watch|listen)\s+(?:more|also|next|related)(?:\.{2,}|\s*(?:here|at)\b|https?://|www\.).*$",
    re.IGNORECASE,
)
_INLINE_TERMINAL_RELATED = re.compile(
    r"\s+(?:[-–—|]\s*)?(?:READ|SEE|WATCH|LISTEN)\s+(?:ALSO|NEXT|MORE|RELATED)\s*:\s*.+$"
)


def render_canonical_body(
    payload: dict[str, Any],
    *,
    normalized_row: dict[str, Any] | None = None,
    enrichment_rows: Iterable[dict[str, Any]] = (),
    rendered_article: RenderedNewsArticle | None = None,
) -> CanonicalBodyArticle:
    return render_body_with_contract(
        payload,
        normalized_row=normalized_row,
        enrichment_rows=enrichment_rows,
        rendered_article=rendered_article,
        contract=BODY_V4_CONTRACT,
        block_classifier=_classify_blocks,
    )


def build_body_v4_rows(
    payload: dict[str, Any],
    normalized_row: dict[str, Any],
    body: CanonicalBodyArticle,
    *,
    previous_rendered_text_hash: str,
    previous_renderer_version: str,
    updated_at_utc: str | datetime | None = None,
) -> dict[str, Any]:
    return build_body_rows(
        payload,
        normalized_row,
        body,
        previous_rendered_text_hash=previous_rendered_text_hash,
        previous_renderer_version=previous_renderer_version,
        updated_at_utc=updated_at_utc,
        contract=BODY_V4_CONTRACT,
    )


def body_purity_reasons(text: str) -> tuple[str, ...]:
    """Independent output guard; it intentionally does not call the classifier."""
    reasons: set[str] = set()
    if _CONTROL.search(text):
        reasons.add("control_character")
    if re.search(r"data:image/|base64,", text, re.IGNORECASE):
        reasons.add("embedded_binary_or_data_uri")
    if re.search(r"(?:^|\n)(?:Title|Teaser|Source\s*\[[^\]]+\])\s*:", text, re.IGNORECASE):
        reasons.add("source_wrapper")
    for line in text.splitlines():
        structural = _structural_text(line)
        if _RELATED_MARKER.fullmatch(structural) or _RELATED_PREFIX.fullmatch(structural) or _BARE_RELATED_CTA.fullmatch(structural):
            reasons.add("related_content_marker")
        if _PROMO.search(structural):
            reasons.add("promotion")
        if _DISCLOSURE.search(structural):
            reasons.add("disclosure")
    if _INLINE_TERMINAL_RELATED.search(text):
        reasons.add("inline_related_content")
    if "�" in text or re.search(r"Ã[\x80-\xbf]", text) or "â€" in text:
        reasons.add("encoding_artifact")
    return tuple(sorted(reasons))


def _classify_blocks(
    blocks: Iterable[NewsBlock], *, allowed_hashes: set[str] | None = None,
) -> Iterable[BodyBlock]:
    seen: set[str] = set()
    exclude_next_related = False
    for block in blocks:
        original = str(block.text or "")
        cleaned = _clean(original)
        structural = _structural_text(cleaned)
        included_text = _trim_terminal_related(cleaned)
        role, disposition, reason = "article_body", "included", "article_content"
        folded_hash = _sha256(cleaned.casefold())
        if not cleaned:
            role, disposition, reason = "empty", "excluded", "empty_after_cleaning"
        elif allowed_hashes is not None and folded_hash not in allowed_hashes:
            role, disposition, reason = "page_chrome", "excluded", "outside_article_container"
        elif block.block_kind == "image" or _IMAGE_JUNK.search(structural):
            role, disposition, reason = "image_metadata", "excluded", "image_metadata_not_body"
        elif _SOURCE_WRAPPER.search(structural):
            role, disposition, reason = "source_wrapper", "excluded", "source_wrapper_not_body"
        elif _RELATED_MARKER.fullmatch(structural):
            role, disposition, reason = "related_content", "excluded", "related_content_marker"
            exclude_next_related = True
        elif _RELATED_PREFIX.fullmatch(structural) or _BARE_RELATED_CTA.fullmatch(structural):
            role, disposition, reason = "related_content", "excluded", "inline_related_content"
            exclude_next_related = False
        elif exclude_next_related and _looks_like_link_title(structural):
            role, disposition, reason = "related_content", "excluded", "related_link_after_marker"
            exclude_next_related = False
        elif _DISCLOSURE.search(structural):
            role, disposition, reason = "disclosure", "excluded", "disclosure_not_body"
            exclude_next_related = False
        elif _PROMO.search(structural):
            role, disposition, reason = "promotion", "excluded", "promotion_not_body"
            exclude_next_related = False
        elif _NAV.search(structural):
            role, disposition, reason = "navigation", "excluded", "navigation_not_body"
            exclude_next_related = False
        elif len(structural) <= 180 and len(_NAV_WORD.findall(structural)) >= 2:
            role, disposition, reason = "navigation", "excluded", "navigation_cluster_not_body"
            exclude_next_related = False
        elif _sha256(included_text.casefold()) in seen:
            role, disposition, reason = "duplicate", "excluded", "duplicate_paragraph"
            exclude_next_related = False
        else:
            if included_text != cleaned:
                reason = "article_content_terminal_related_removed"
            cleaned = included_text
            seen.add(_sha256(cleaned.casefold()))
            if exclude_next_related:
                exclude_next_related = False
        yield BodyBlock(
            source_kind=block.source_kind,
            source_ordinal=block.source_ordinal,
            block_ordinal=block.block_ordinal,
            block_kind=block.block_kind,
            original_text=original,
            cleaned_text=cleaned,
            block_role=role,
            disposition=disposition,
            reason=reason,
            original_hash=_sha256(original),
            cleaned_hash=_sha256(cleaned),
            table_ordinal=block.table_ordinal,
            table_row_ordinal=block.table_row_ordinal,
        )


def _structural_text(value: str) -> str:
    return _STRUCTURAL_PREFIX.sub("", value.strip()).strip()


def _trim_terminal_related(value: str) -> str:
    match = _INLINE_TERMINAL_RELATED.search(value)
    return value[: match.start()].rstrip(" -–—|") if match else value


def contract_manifest() -> dict[str, str]:
    material = {
        "text_contract": BODY_TEXT_CONTRACT,
        "source_selection_version": BODY_SOURCE_SELECTION_VERSION,
        "cleaner_version": BODY_CLEANER_VERSION,
        "renderer_version": BODY_RENDERER_VERSION,
    }
    return {**material, "contract_hash": _sha256(json.dumps(material, sort_keys=True))}

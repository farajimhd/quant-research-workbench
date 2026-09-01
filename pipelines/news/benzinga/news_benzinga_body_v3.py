from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Iterable

from pipelines.news.benzinga.core.clickhouse_values import datetime64_utc_text
from pipelines.news.benzinga.news_benzinga_render_v2 import (
    NewsBlock,
    NewsSource,
    RenderedNewsArticle,
    render_news_article,
    render_news_source,
)


BODY_SOURCE_SELECTION_VERSION = "benzinga_body_source_selection_v1"
BODY_CLEANER_VERSION = "benzinga_body_cleaner_v3"
BODY_RENDERER_VERSION = "benzinga_body_renderer_v3"
BODY_TEXT_CONTRACT = "benzinga_canonical_body_only_v1"


@dataclass(frozen=True, slots=True)
class BodyContract:
    source_selection_version: str
    cleaner_version: str
    renderer_version: str
    text_contract: str


BODY_V3_CONTRACT = BodyContract(
    source_selection_version=BODY_SOURCE_SELECTION_VERSION,
    cleaner_version=BODY_CLEANER_VERSION,
    renderer_version=BODY_RENDERER_VERSION,
    text_contract=BODY_TEXT_CONTRACT,
)

MIN_PROVIDER_BODY_CHARS = 20
MIN_FALLBACK_BODY_CHARS = 120
MIN_COMPLETE_PROVIDER_CHARS = 280

_RELATED_MARKER = re.compile(
    r"^(?:read|see|watch|listen)(?:\s+(?:also|next|more|related))?\s*[,:;.!?\"'’”»›-]*\s*$",
    re.IGNORECASE,
)
_RELATED_PREFIX = re.compile(
    r"^(?:(?:read|see|watch|listen)\s+(?:also|next|more|related)\b\s*(?::|>|-|,|;|\.|\"|”|\s)\s*.+|"
    r"read\s+full\s+article\b.*)$",
    re.IGNORECASE,
)
_PROMO = re.compile(
    r"^(?:get\s+benzinga\s+pro|benzinga\s+pro|click\s+here|sign\s+up(?:\s+now|\s+today|\s+for)?|"
    r"subscribe(?:\s+now|\s+today|\s+to\s+(?:our|the))?|download\s+(?:our|the)\s+app|"
    r"join\s+(?:our|the)\s+newsletter|never\s+miss|get\s+(?:the\s+)?latest|"
    r"check\s+out\s+(?:more|our)|powered\s+by\s+benzinga)\b",
    re.IGNORECASE,
)
_NAV = re.compile(
    r"^(?:home|markets|news|stocks|crypto|earnings|analyst ratings|options|etfs|"
    r"privacy policy|terms of use|cookie policy|contact us|about us)(?:\s*[|•·]\s*.+)+$",
    re.IGNORECASE,
)
_NAV_WORD = re.compile(
    r"\b(?:skip to content|accessibility|menu|sign in|sign out|create an account|help|home|"
    r"privacy|terms|contact|country|cloud account)\b",
    re.IGNORECASE,
)
_DISCLOSURE = re.compile(
    r"^(?:disclosure|disclaimer|editor['’]s note|photo(?:graph)?(?:\s+credit)?|image\s+(?:via|credit)|"
    r"this content was partially produced|the author has no position|benzinga does not provide investment advice|"
    r"readers are advised|this (?:article|information) is (?:solely )?for information(?:al)? purposes|"
    r"not intended to be used as the sole basis of any investment)",
    re.IGNORECASE,
)
_IMAGE_JUNK = re.compile(
    r"^(?:image\s*:|photo\s+(?:by|via)|shutterstock|istock|depositphotos|"
    r"data:image/|[A-Za-z0-9+/]{160,}={0,2}$)",
    re.IGNORECASE,
)
_SOURCE_WRAPPER = re.compile(r"^(?:title|teaser|source)\s*(?:\[[^\]]+\])?\s*:", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MOJIBAKE_REPLACEMENTS = (
    ("\u00e2\u00c3\u0082\u0080\u00c3\u0082\u0099", "’"),
    ("\u00e2\u00c3\u0082\u0089\u00a5", "≥"),
    ("\u00e2\u00c3\u0082\u0080", "—"),
)


@dataclass(frozen=True, slots=True)
class BodyBlock:
    source_kind: str
    source_ordinal: int
    block_ordinal: int
    block_kind: str
    original_text: str
    cleaned_text: str
    block_role: str
    disposition: str
    reason: str
    original_hash: str
    cleaned_hash: str
    table_ordinal: int = 0
    table_row_ordinal: int = 0


@dataclass(frozen=True, slots=True)
class BodySource:
    source: NewsSource
    source_role: str
    disposition: str
    reason: str
    identity_score: float
    blocks: tuple[BodyBlock, ...]


@dataclass(frozen=True, slots=True)
class CanonicalBodyArticle:
    canonical_body_text: str
    display_text: str
    body_hash: str
    body_status: str
    primary_source_kind: str
    primary_source_ordinal: int
    source_revision_key: str
    sources: tuple[BodySource, ...]
    quality_flags: tuple[str, ...]


def render_canonical_body(
    payload: dict[str, Any],
    *,
    normalized_row: dict[str, Any] | None = None,
    enrichment_rows: Iterable[dict[str, Any]] = (),
    rendered_article: RenderedNewsArticle | None = None,
    contract: BodyContract = BODY_V3_CONTRACT,
    block_classifier: Any = None,
) -> CanonicalBodyArticle:
    """Build one deterministic body authority while retaining every disposition."""
    normalized = normalized_row or {}
    enrichment_rows = tuple(enrichment_rows)
    title = _clean(str(payload.get("title") or normalized.get("title") or ""))
    legacy = rendered_article or render_news_article(
        payload, normalized_row=normalized, enrichment_rows=enrichment_rows,
    )
    allowed_by_source = _article_container_allowlist(payload, enrichment_rows)
    classifier = block_classifier or _classify_blocks
    classified: list[BodySource] = []
    for source in legacy.sources:
        allowed = allowed_by_source.get((source.source_kind, source.source_url))
        blocks = tuple(classifier(source.blocks, allowed_hashes=allowed))
        classified.append(
            BodySource(
                source=source,
                source_role=_default_source_role(source),
                disposition="supporting",
                reason="supporting_source_not_body_authority",
                identity_score=_source_identity_score(title, source),
                blocks=blocks,
            )
        )

    provider_index = next(
        (index for index, item in enumerate(classified) if item.source.source_kind == "provider_body"),
        None,
    )
    primary_index: int | None = None
    if provider_index is not None:
        provider_text = _included_text(classified[provider_index].blocks)
        if len(provider_text) >= MIN_PROVIDER_BODY_CHARS:
            primary_index = provider_index
    if primary_index is None:
        candidates = [
            (index, item)
            for index, item in enumerate(classified)
            if item.source.source_kind in {"external", "pdf"}
            and not _legacy_source(item.source)
            and len(_included_text(item.blocks)) >= MIN_FALLBACK_BODY_CHARS
            and item.identity_score >= 0.72
        ]
        if candidates:
            primary_index = max(candidates, key=lambda value: (value[1].identity_score, -value[0]))[0]

    resolved_sources: list[BodySource] = []
    for index, item in enumerate(classified):
        if index == primary_index:
            reason = "provider_body_default" if item.source.source_kind == "provider_body" else "strict_identity_fallback"
            resolved_sources.append(
                BodySource(item.source, "primary_body", "included", reason, item.identity_score, item.blocks)
            )
        else:
            reason = item.reason
            disposition = "supporting"
            if _legacy_source(item.source):
                disposition, reason = "excluded", "legacy_flattened_source_not_promotable"
            elif item.source.source_kind == "provider_body":
                disposition, reason = "excluded", "provider_body_missing_or_not_substantive"
            resolved_sources.append(
                BodySource(item.source, item.source_role, disposition, reason, item.identity_score, item.blocks)
            )

    primary = resolved_sources[primary_index] if primary_index is not None else None
    body = _included_text(primary.blocks) if primary else ""
    status = "complete" if len(body) >= MIN_COMPLETE_PROVIDER_CHARS else "partial" if body else "missing"
    flags = set(legacy.quality_flags)
    if status == "missing":
        flags.add("canonical_body_missing")
    elif status == "partial":
        flags.add("canonical_body_partial")
    if primary and primary.source.source_kind != "provider_body":
        flags.add("supporting_source_promoted")
    revision_material = "\0".join(
        [
            str(normalized.get("raw_payload_hash") or ""),
            contract.source_selection_version,
            contract.cleaner_version,
            contract.renderer_version,
            *[
                f"{item.source.source_kind}:{item.source.source_ordinal}:{item.source.source_hash}:"
                f"{item.disposition}:{item.reason}"
                for item in resolved_sources
            ],
        ]
    )
    return CanonicalBodyArticle(
        canonical_body_text=body,
        display_text=body,
        body_hash=_sha256(body),
        body_status=status,
        primary_source_kind=primary.source.source_kind if primary else "",
        primary_source_ordinal=primary.source.source_ordinal if primary else 0,
        source_revision_key=_sha256(revision_material),
        sources=tuple(resolved_sources),
        quality_flags=tuple(sorted(flags)),
    )


def build_body_v3_rows(
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
        contract=BODY_V3_CONTRACT,
    )


def build_body_rows(
    payload: dict[str, Any],
    normalized_row: dict[str, Any],
    body: CanonicalBodyArticle,
    *,
    previous_rendered_text_hash: str,
    previous_renderer_version: str,
    updated_at_utc: str | datetime | None = None,
    contract: BodyContract,
) -> dict[str, Any]:
    updated = datetime64_utc_text(updated_at_utc)
    canonical = str(normalized_row["canonical_news_id"])
    published_date = str(normalized_row["published_date"])
    event = {
        key: normalized_row.get(key)
        for key in (
            "provider", "provider_article_id", "canonical_news_id", "published_date",
            "published_at_utc", "published_raw", "last_updated_at_utc", "last_updated_raw",
            "downloaded_at_utc", "provider_delay_ns", "title", "normalized_title", "teaser",
            "article_url", "url_domain", "author", "tickers", "channels", "provider_tags",
            "image_urls", "links", "raw_artifact_path", "raw_payload_hash",
        )
    }
    event.update(
        {
            "source_revision_key": body.source_revision_key,
            "source_selection_version": contract.source_selection_version,
            "cleaner_version": contract.cleaner_version,
            "renderer_version": contract.renderer_version,
            "content_quality_flags": sorted(set(normalized_row.get("content_quality_flags") or []) | set(body.quality_flags)),
            "updated_at_utc": updated,
        }
    )
    sources: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    for item in body.sources:
        source = item.source
        cleaned_source = _included_text(item.blocks)
        sources.append(
            {
                "canonical_news_id": canonical,
                "published_date": published_date,
                "source_kind": source.source_kind,
                "source_ordinal": source.source_ordinal,
                "source_role": item.source_role,
                "disposition": item.disposition,
                "disposition_reason": item.reason,
                "identity_score": round(item.identity_score, 6),
                "source_url": source.source_url,
                "artifact_path": source.artifact_path,
                "content_format": source.content_format,
                "original_hash": source.source_hash,
                "original_chars": source.source_chars,
                "cleaned_text": cleaned_source,
                "cleaned_hash": _sha256(cleaned_source),
                "cleaned_chars": len(cleaned_source),
                "block_count": len(item.blocks),
                "included_block_count": sum(block.disposition == "included" for block in item.blocks),
                "quality_flags": list(source.quality_flags),
                "source_selection_version": contract.source_selection_version,
                "cleaner_version": contract.cleaner_version,
                "renderer_version": contract.renderer_version,
                "source_revision_key": body.source_revision_key,
                "updated_at_utc": updated,
            }
        )
        for block in item.blocks:
            blocks.append(
                {
                    "canonical_news_id": canonical,
                    "published_date": published_date,
                    "source_kind": block.source_kind,
                    "source_ordinal": block.source_ordinal,
                    "block_ordinal": block.block_ordinal,
                    "block_kind": block.block_kind,
                    "block_role": block.block_role,
                    "disposition": block.disposition,
                    "disposition_reason": block.reason,
                    "original_text": block.original_text,
                    "cleaned_text": block.cleaned_text,
                    "original_hash": block.original_hash,
                    "cleaned_hash": block.cleaned_hash,
                    "table_ordinal": block.table_ordinal,
                    "table_row_ordinal": block.table_row_ordinal,
                    "cleaner_version": contract.cleaner_version,
                    "source_revision_key": body.source_revision_key,
                    "updated_at_utc": updated,
                }
            )
    rendered = {
        "canonical_news_id": canonical,
        "provider_article_id": str(normalized_row["provider_article_id"]),
        "published_date": published_date,
        "published_at_utc": normalized_row["published_at_utc"],
        "title": _clean(str(payload.get("title") or normalized_row.get("title") or "")),
        "canonical_body_text": body.canonical_body_text,
        "display_text": body.display_text,
        "body_hash": body.body_hash,
        "body_status": body.body_status,
        "primary_source_kind": body.primary_source_kind,
        "primary_source_ordinal": body.primary_source_ordinal,
        "source_revision_key": body.source_revision_key,
        "source_count": len(body.sources),
        "included_source_count": sum(item.disposition == "included" for item in body.sources),
        "supporting_source_count": sum(item.disposition == "supporting" for item in body.sources),
        "excluded_source_count": sum(item.disposition == "excluded" for item in body.sources),
        "included_block_count": sum(
            block.disposition == "included" for item in body.sources for block in item.blocks
        ),
        "excluded_block_count": sum(
            block.disposition == "excluded" for item in body.sources for block in item.blocks
        ),
        "source_selection_version": contract.source_selection_version,
        "cleaner_version": contract.cleaner_version,
        "renderer_version": contract.renderer_version,
        "text_contract": contract.text_contract,
        "quality_flags": list(body.quality_flags),
        "updated_at_utc": updated,
    }
    unique_tickers = list(dict.fromkeys(
        str(value).strip().upper() for value in normalized_row.get("tickers") or [] if str(value).strip()
    ))
    tickers = [
        {
            "canonical_news_id": canonical,
            "provider_article_id": str(normalized_row["provider_article_id"]),
            "published_date": published_date,
            "published_at_utc": normalized_row["published_at_utc"],
            "ticker": ticker,
            "ticker_index": index,
            "ticker_count": len(unique_tickers),
            "body_hash": body.body_hash,
            "source_revision_key": body.source_revision_key,
            "renderer_version": contract.renderer_version,
            "updated_at_utc": updated,
        }
        for index, ticker in enumerate(unique_tickers, start=1)
    ]
    lineage = {
        "canonical_news_id": canonical,
        "provider_article_id": str(normalized_row["provider_article_id"]),
        "published_date": published_date,
        "previous_rendered_text_hash": previous_rendered_text_hash,
        "previous_renderer_version": previous_renderer_version,
        "body_hash": body.body_hash,
        "body_renderer_version": contract.renderer_version,
        "source_revision_key": body.source_revision_key,
        "label_mutation_status": "not_mutated",
        "updated_at_utc": updated,
    }
    return {"event": event, "sources": sources, "blocks": blocks, "rendered": rendered, "tickers": tickers, "lineage": lineage}


def body_purity_reasons(text: str) -> tuple[str, ...]:
    reasons: set[str] = set()
    if _CONTROL.search(text):
        reasons.add("control_character")
    if re.search(r"data:image/|base64,", text, re.IGNORECASE):
        reasons.add("embedded_binary_or_data_uri")
    if re.search(r"(?:^|\n)(?:Title|Teaser|Source\s*\[[^\]]+\])\s*:", text, re.IGNORECASE):
        reasons.add("source_wrapper")
    if re.search(r"(?im)^(?:read|see|watch|listen)(?:\s+(?:also|next|more|related))?\s*:?[\s»›-]*$", text) or any(
        _RELATED_PREFIX.search(line.strip()) for line in text.splitlines()
    ):
        reasons.add("related_content_marker")
    if any(_PROMO.search(line.strip()) for line in text.splitlines()):
        reasons.add("promotion")
    if any(_DISCLOSURE.search(line.strip()) for line in text.splitlines()):
        reasons.add("disclosure")
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
        role, disposition, reason = "article_body", "included", "article_content"
        folded_hash = _sha256(cleaned.casefold())
        if not cleaned:
            role, disposition, reason = "empty", "excluded", "empty_after_cleaning"
        elif allowed_hashes is not None and folded_hash not in allowed_hashes:
            role, disposition, reason = "page_chrome", "excluded", "outside_article_container"
        elif block.block_kind == "image" or _IMAGE_JUNK.search(cleaned):
            role, disposition, reason = "image_metadata", "excluded", "image_metadata_not_body"
        elif _SOURCE_WRAPPER.search(cleaned):
            role, disposition, reason = "source_wrapper", "excluded", "source_wrapper_not_body"
        elif _RELATED_MARKER.match(cleaned):
            role, disposition, reason = "related_content", "excluded", "related_content_marker"
            exclude_next_related = True
        elif _RELATED_PREFIX.match(cleaned):
            role, disposition, reason = "related_content", "excluded", "inline_related_content"
        elif exclude_next_related and _looks_like_link_title(cleaned):
            role, disposition, reason = "related_content", "excluded", "related_link_after_marker"
            exclude_next_related = False
        elif _DISCLOSURE.search(cleaned):
            role, disposition, reason = "disclosure", "excluded", "disclosure_not_body"
            exclude_next_related = False
        elif _PROMO.search(cleaned):
            role, disposition, reason = "promotion", "excluded", "promotion_not_body"
            exclude_next_related = False
        elif _NAV.search(cleaned):
            role, disposition, reason = "navigation", "excluded", "navigation_not_body"
            exclude_next_related = False
        elif len(cleaned) <= 180 and len(_NAV_WORD.findall(cleaned)) >= 2:
            role, disposition, reason = "navigation", "excluded", "navigation_cluster_not_body"
            exclude_next_related = False
        elif folded_hash in seen:
            role, disposition, reason = "duplicate", "excluded", "duplicate_paragraph"
            exclude_next_related = False
        else:
            seen.add(folded_hash)
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


def _included_text(blocks: Iterable[BodyBlock]) -> str:
    return "\n".join(block.cleaned_text for block in blocks if block.disposition == "included" and block.cleaned_text).strip()


def _article_container_allowlist(
    payload: dict[str, Any], enrichment_rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], set[str]]:
    candidates: list[tuple[str, str, str]] = []
    provider_html = str(payload.get("body") or "")
    provider_url = str(payload.get("url") or "")
    if provider_html:
        candidates.append(("provider_body", provider_url, provider_html))
    for row in enrichment_rows:
        raw_html = str(row.get("raw_html") or "")
        if raw_html:
            candidates.append(("external", str(row.get("final_url") or row.get("normalized_url") or ""), raw_html))
    output: dict[tuple[str, str], set[str]] = {}
    for source_kind, source_url, source_html in candidates:
        container = _first_article_container(source_html)
        if not container:
            continue
        rendered = render_news_source(
            container,
            source_kind=source_kind,
            source_ordinal=0,
            source_url=source_url,
            artifact_path="",
            content_format="html",
        )
        output[(source_kind, source_url)] = {
            _sha256(_clean(block.text).casefold()) for block in rendered.blocks if _clean(block.text)
        }
    return output


def _first_article_container(source_html: str) -> str:
    for tag in ("article", "main"):
        match = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}\s*>", source_html)
        if match:
            return match.group(1)
    return ""


def _source_identity_score(title: str, source: NewsSource) -> float:
    if source.source_kind == "provider_body":
        return 1.0
    if not title:
        return 0.0
    title_tokens = _tokens(title)
    candidate = source.rendered_text[:1600]
    candidate_tokens = _tokens(candidate)
    overlap = len(title_tokens & candidate_tokens) / max(1, len(title_tokens))
    sequence = SequenceMatcher(None, title.casefold(), candidate[: max(len(title) * 3, 160)].casefold()).ratio()
    return min(1.0, 0.8 * overlap + 0.2 * sequence)


def _default_source_role(source: NewsSource) -> str:
    return "provider_candidate" if source.source_kind == "provider_body" else "supporting_document"


def _legacy_source(source: NewsSource) -> bool:
    flags = set(source.quality_flags)
    return "legacy_flattened_enrichment" in flags or (
        source.source_kind in {"external", "pdf"} and not source.source_url and not source.artifact_path
    )


def _looks_like_link_title(value: str) -> bool:
    words = value.split()
    if not 2 <= len(words) <= 24 or len(value) > 180 or value.endswith((".", "!", "?")):
        return False
    capitalized = sum(word[:1].isupper() for word in words if word[:1].isalpha())
    return capitalized >= max(2, int(len(words) * 0.55))


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 2}


def _clean(value: str) -> str:
    value = html.unescape(value or "")
    for broken, repaired in _MOJIBAKE_REPLACEMENTS:
        value = value.replace(broken, repaired)
    value = value.replace('â€"', "—")
    value = re.sub(r"â€(?=\s|[.,;:!?])", "”", value)
    value = re.sub(r"\s+�\s+", " — ", value)
    value = value.replace("�", "")
    value = _CONTROL.sub("", value)
    value = re.sub(r"data:image/[^\s\"']+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def contract_manifest() -> dict[str, str]:
    return {
        "text_contract": BODY_TEXT_CONTRACT,
        "source_selection_version": BODY_SOURCE_SELECTION_VERSION,
        "cleaner_version": BODY_CLEANER_VERSION,
        "renderer_version": BODY_RENDERER_VERSION,
        "contract_hash": _sha256(json.dumps(
            {
                "text_contract": BODY_TEXT_CONTRACT,
                "source_selection_version": BODY_SOURCE_SELECTION_VERSION,
                "cleaner_version": BODY_CLEANER_VERSION,
                "renderer_version": BODY_RENDERER_VERSION,
            },
            sort_keys=True,
        )),
    }

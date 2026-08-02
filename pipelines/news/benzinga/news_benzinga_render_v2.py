from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from pipelines.news.benzinga.core.content_quality import transport_artifact_reasons
from pipelines.news.benzinga.core.clickhouse_values import datetime64_utc_text
from pipelines.sec.edgar.sec_pipeline.text_renderer import (
    SEC_PACKED_TEXT_RENDERER_VERSION,
    PackedTextResult,
    render_sec_packed_text,
)


NEWS_RENDERER_VERSION = f"benzinga_structured_renderer_v3+{SEC_PACKED_TEXT_RENDERER_VERSION}"
NEWS_RENDERED_TEXT_CONTRACT = "benzinga_article_sources_block_packed_v3"


@dataclass(frozen=True, slots=True)
class NewsBlock:
    source_kind: str
    source_ordinal: int
    block_ordinal: int
    block_kind: str
    text: str
    table_ordinal: int = 0
    table_row_ordinal: int = 0


@dataclass(frozen=True, slots=True)
class NewsSource:
    source_kind: str
    source_ordinal: int
    source_url: str
    artifact_path: str
    content_format: str
    source_hash: str
    source_chars: int
    rendered_text: str
    rendered_hash: str
    blocks: tuple[NewsBlock, ...]
    table_count: int
    quality_flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenderedNewsArticle:
    packed_text: str
    packed_text_hash: str
    source_revision_key: str
    sources: tuple[NewsSource, ...]
    blocks: tuple[NewsBlock, ...]
    quality_flags: tuple[str, ...]


class _MediaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = {key.lower(): html.unescape(value or "").strip() for key, value in attrs}
        src = values.get("src") or values.get("data-src") or ""
        alt = values.get("alt", "")
        title = values.get("title", "")
        if src or alt or title:
            self.images.append((src, alt, title))


def render_news_article(
    payload: dict[str, Any],
    *,
    normalized_row: dict[str, Any] | None = None,
    enrichment_rows: Iterable[dict[str, Any]] = (),
) -> RenderedNewsArticle:
    normalized = normalized_row or {}
    title = _clean_inline(str(payload.get("title") or normalized.get("title") or ""))
    teaser = _clean_inline(str(payload.get("teaser") or normalized.get("teaser") or ""))
    body_html = str(payload.get("body") or "")
    article_url = str(payload.get("url") or normalized.get("article_url") or "")
    raw_hash = str(normalized.get("raw_payload_hash") or _sha256_json(payload))

    sources: list[NewsSource] = []
    rejected_quality_flags: set[str] = set()
    if body_html:
        sources.append(
            render_news_source(
                body_html,
                source_kind="provider_body",
                source_ordinal=0,
                source_url=article_url,
                artifact_path=str(normalized.get("raw_artifact_path") or ""),
                content_format="html",
            )
        )
    elif normalized.get("body_text"):
        sources.append(
            render_news_source(
                str(normalized["body_text"]),
                source_kind="provider_body",
                source_ordinal=0,
                source_url=article_url,
                artifact_path=str(normalized.get("raw_artifact_path") or ""),
                content_format="plain_text",
                additional_flags=("provider_raw_artifact_unavailable", "legacy_flattened_provider_body"),
            )
        )

    external_ordinal = 0
    pdf_ordinal = 0
    seen_enrichment_hashes: set[str] = set()
    for row in enrichment_rows:
        text = str(row.get("structured_text") or row.get("extracted_text") or "")
        if not text:
            continue
        source_hash = str(row.get("fetched_sha256") or row.get("extracted_text_hash") or _sha256(text))
        if source_hash in seen_enrichment_hashes:
            continue
        seen_enrichment_hashes.add(source_hash)
        action = str(row.get("resolved_action") or row.get("final_action") or "")
        is_pdf = action == "fetch_pdf" or str(row.get("extraction_method") or "").startswith("pdf")
        if is_pdf:
            pdf_ordinal += 1
            kind = "pdf"
            ordinal = pdf_ordinal
            fmt = "plain_text"
        else:
            external_ordinal += 1
            kind = "external"
            ordinal = external_ordinal
            fmt = "html" if row.get("raw_html") else "plain_text"
            if row.get("raw_html"):
                text = str(row["raw_html"])
            if transport_artifact_reasons(text):
                rejected_quality_flags.add("external_transport_artifact_rejected")
                continue
        artifact_path = str(row.get("artifact_path") or "")
        if is_pdf and artifact_path and Path(artifact_path).exists():
            source = render_pdf_artifact(
                Path(artifact_path),
                source_ordinal=ordinal,
                source_url=str(row.get("final_url") or row.get("normalized_url") or ""),
                fallback_text=text,
            )
        else:
            source = render_news_source(
                text,
                source_kind=kind,
                source_ordinal=ordinal,
                source_url=str(row.get("final_url") or row.get("normalized_url") or ""),
                artifact_path=artifact_path,
                content_format=fmt,
                additional_flags=(() if row.get("raw_html") else ("legacy_flattened_enrichment",)),
            )
        sources.append(source)

    # Historical v1 enrichment text is retained as explicitly provenance-marked
    # plain text when the source artifact is unavailable. It is never presented
    # as structurally recovered HTML/PDF.
    if normalized.get("external_text") and not any(source.source_kind == "external" for source in sources):
        legacy_external_text = str(normalized["external_text"])
        if transport_artifact_reasons(legacy_external_text):
            rejected_quality_flags.add("external_transport_artifact_rejected")
        else:
            sources.append(
                render_news_source(
                    legacy_external_text,
                    source_kind="external",
                    source_ordinal=1,
                    source_url="",
                    artifact_path="",
                    content_format="plain_text",
                    additional_flags=(
                        "legacy_flattened_enrichment",
                        "external_source_artifact_unavailable",
                    ),
                )
            )
    if normalized.get("pdf_text") and not any(source.source_kind == "pdf" for source in sources):
        artifact = _first_string(normalized.get("pdf_artifact_paths"))
        pdf_path = Path(artifact) if artifact else None
        if pdf_path is not None and pdf_path.exists():
            sources.append(
                render_pdf_artifact(
                    pdf_path,
                    source_ordinal=1,
                    source_url="",
                    fallback_text=str(normalized["pdf_text"]),
                )
            )
        else:
            sources.append(
                render_news_source(
                    str(normalized["pdf_text"]),
                    source_kind="pdf",
                    source_ordinal=1,
                    source_url="",
                    artifact_path=artifact,
                    content_format="plain_text",
                    additional_flags=("legacy_flattened_enrichment", "pdf_structure_unavailable"),
                )
            )

    article_parts: list[str] = []
    if title:
        article_parts.append(f"Title: {title}")
    if teaser and teaser.casefold() != title.casefold():
        article_parts.append(f"Teaser: {teaser}")
    all_blocks: list[NewsBlock] = []
    flags: set[str] = set(rejected_quality_flags)
    for source in sources:
        article_parts.append(
            f"Source [{source.source_kind}:{source.source_ordinal}]"
            + (f" {source.source_url}" if source.source_url else "")
        )
        article_parts.append(source.rendered_text)
        all_blocks.extend(source.blocks)
        flags.update(source.quality_flags)
    if not sources:
        flags.add("no_renderable_sources")
    packed = "\n".join(part for part in article_parts if part).strip()
    packed_hash = _sha256(packed)
    revision_material = "\0".join(
        [
            raw_hash,
            NEWS_RENDERER_VERSION,
            *[f"{source.source_kind}:{source.source_ordinal}:{source.source_hash}" for source in sources],
        ]
    )
    return RenderedNewsArticle(
        packed_text=packed,
        packed_text_hash=packed_hash,
        source_revision_key=_sha256(revision_material),
        sources=tuple(sources),
        blocks=tuple(all_blocks),
        quality_flags=tuple(sorted(flags)),
    )


def render_news_source(
    source_text: str,
    *,
    source_kind: str,
    source_ordinal: int,
    source_url: str,
    artifact_path: str,
    content_format: str,
    additional_flags: Iterable[str] = (),
) -> NewsSource:
    repaired_source = _repair_mojibake(source_text)
    encoding_repaired = repaired_source != source_text
    source_text = repaired_source
    result = render_sec_packed_text(
        source_text,
        content_format,
        document_name=source_url,
        document_type=source_kind,
        text_kind=source_kind,
        include_intermediate=True,
    )
    blocks = list(_blocks_from_result(result, source_kind, source_ordinal))
    if content_format == "html":
        blocks.extend(_image_blocks(source_text, source_kind, source_ordinal, len(blocks)))
    rendered_text = "\n".join(block.text for block in blocks if block.text).strip()
    flags = set(result.quality_flags)
    flags.update(str(flag) for flag in additional_flags if str(flag))
    if encoding_repaired:
        flags.add("mojibake_repaired")
    if not rendered_text and source_text.strip():
        flags.add("unexpected_empty_render")
    return NewsSource(
        source_kind=source_kind,
        source_ordinal=source_ordinal,
        source_url=source_url,
        artifact_path=artifact_path,
        content_format=content_format,
        source_hash=_sha256(source_text),
        source_chars=len(source_text),
        rendered_text=rendered_text,
        rendered_hash=_sha256(rendered_text),
        blocks=tuple(blocks),
        table_count=result.table_block_count,
        quality_flags=tuple(sorted(flags)),
    )


def render_pdf_artifact(
    path: Path,
    *,
    source_ordinal: int,
    source_url: str,
    fallback_text: str = "",
) -> NewsSource:
    """Preserve PDF page and reading-order block boundaries when the artifact exists."""
    raw = path.read_bytes()
    blocks: list[NewsBlock] = []
    flags: set[str] = set()
    try:
        import fitz  # type: ignore

        document = fitz.open(stream=raw, filetype="pdf")
        for page_index, page in enumerate(document, start=1):
            blocks.append(
                NewsBlock(
                    source_kind="pdf",
                    source_ordinal=source_ordinal,
                    block_ordinal=len(blocks),
                    block_kind="pdf_page",
                    text=f"Page {page_index}",
                )
            )
            for raw_block in page.get_text("blocks", sort=True):
                text = re.sub(r"[ \t]+", " ", str(raw_block[4] or ""))
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if text:
                    blocks.append(
                        NewsBlock(
                            source_kind="pdf",
                            source_ordinal=source_ordinal,
                            block_ordinal=len(blocks),
                            block_kind="paragraph",
                            text=text,
                        )
                    )
        document.close()
    except Exception as exc:  # noqa: BLE001
        flags.update(("pdf_structured_extract_failed", type(exc).__name__))
    if not blocks and fallback_text.strip():
        flags.add("pdf_flattened_fallback")
        blocks.append(
            NewsBlock(
                source_kind="pdf",
                source_ordinal=source_ordinal,
                block_ordinal=0,
                block_kind="paragraph",
                text=fallback_text.strip(),
            )
        )
    rendered = "\n".join(block.text for block in blocks).strip()
    if not rendered:
        flags.add("unexpected_empty_render")
    return NewsSource(
        source_kind="pdf",
        source_ordinal=source_ordinal,
        source_url=source_url,
        artifact_path=str(path),
        content_format="pdf",
        source_hash=hashlib.sha256(raw).hexdigest(),
        source_chars=len(raw),
        rendered_text=rendered,
        rendered_hash=_sha256(rendered),
        blocks=tuple(blocks),
        table_count=0,
        quality_flags=tuple(sorted(flags)),
    )


def build_v2_rows(
    payload: dict[str, Any],
    normalized_row: dict[str, Any],
    rendered: RenderedNewsArticle,
    *,
    updated_at_utc: str | datetime | None = None,
) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
    updated = datetime64_utc_text(updated_at_utc)
    canonical = str(normalized_row["canonical_news_id"])
    published_date = str(normalized_row["published_date"])
    event = {
        key: normalized_row.get(key)
        for key in (
            "provider",
            "provider_article_id",
            "canonical_news_id",
            "published_date",
            "published_at_utc",
            "published_raw",
            "last_updated_at_utc",
            "last_updated_raw",
            "downloaded_at_utc",
            "provider_delay_ns",
            "title",
            "normalized_title",
            "teaser",
            "article_url",
            "url_domain",
            "author",
            "tickers",
            "channels",
            "provider_tags",
            "image_urls",
            "links",
            "raw_artifact_path",
            "raw_payload_hash",
        )
    }
    repaired_title = _clean_inline(str(payload.get("title") or normalized_row.get("title") or ""))
    repaired_teaser = _clean_inline(str(payload.get("teaser") or normalized_row.get("teaser") or ""))
    repaired_author = _clean_inline(str(normalized_row.get("author") or ""))
    event.update(
        {
            "title": repaired_title,
            "normalized_title": repaired_title.casefold(),
            "teaser": repaired_teaser,
            "author": repaired_author,
            "source_revision_key": rendered.source_revision_key,
            "renderer_version": NEWS_RENDERER_VERSION,
            "content_quality_flags": sorted(set(normalized_row.get("content_quality_flags") or []) | set(rendered.quality_flags)),
            "updated_at_utc": updated,
        }
    )
    source_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    for source in rendered.sources:
        source_rows.append(
            {
                "canonical_news_id": canonical,
                "published_date": published_date,
                "source_kind": source.source_kind,
                "source_ordinal": source.source_ordinal,
                "source_url": source.source_url,
                "artifact_path": source.artifact_path,
                "content_format": source.content_format,
                "source_hash": source.source_hash,
                "source_chars": source.source_chars,
                "rendered_text": source.rendered_text,
                "rendered_hash": source.rendered_hash,
                "block_count": len(source.blocks),
                "table_block_count": source.table_count,
                "quality_flags": list(source.quality_flags),
                "renderer_version": NEWS_RENDERER_VERSION,
                "source_revision_key": rendered.source_revision_key,
                "updated_at_utc": updated,
            }
        )
        for block in source.blocks:
            block_rows.append(
                {
                    "canonical_news_id": canonical,
                    "published_date": published_date,
                    "source_kind": block.source_kind,
                    "source_ordinal": block.source_ordinal,
                    "block_ordinal": block.block_ordinal,
                    "block_kind": block.block_kind,
                    "block_text": block.text,
                    "block_hash": _sha256(block.text),
                    "table_ordinal": block.table_ordinal,
                    "table_row_ordinal": block.table_row_ordinal,
                    "renderer_version": NEWS_RENDERER_VERSION,
                    "source_revision_key": rendered.source_revision_key,
                    "updated_at_utc": updated,
                }
            )
    rendered_row = {
        "canonical_news_id": canonical,
        "provider_article_id": str(normalized_row["provider_article_id"]),
        "published_date": published_date,
        "published_at_utc": normalized_row["published_at_utc"],
        "title": str(normalized_row["title"]),
        "rendered_text": rendered.packed_text,
        "rendered_text_hash": rendered.packed_text_hash,
        "source_revision_key": rendered.source_revision_key,
        "source_count": len(rendered.sources),
        "block_count": len(rendered.blocks),
        "renderer_version": NEWS_RENDERER_VERSION,
        "text_contract": NEWS_RENDERED_TEXT_CONTRACT,
        "quality_flags": list(rendered.quality_flags),
        "updated_at_utc": updated,
    }
    ticker_rows = []
    tickers = [str(value).strip().upper() for value in normalized_row.get("tickers") or [] if str(value).strip()]
    for index, ticker in enumerate(dict.fromkeys(tickers), start=1):
        ticker_rows.append(
            {
                "canonical_news_id": canonical,
                "provider_article_id": str(normalized_row["provider_article_id"]),
                "published_date": published_date,
                "published_at_utc": normalized_row["published_at_utc"],
                "ticker": ticker,
                "ticker_index": index,
                "ticker_count": len(dict.fromkeys(tickers)),
                "rendered_text_hash": rendered.packed_text_hash,
                "source_revision_key": rendered.source_revision_key,
                "renderer_version": NEWS_RENDERER_VERSION,
                "updated_at_utc": updated,
            }
        )
    return {
        "event": event,
        "sources": source_rows,
        "blocks": block_rows,
        "rendered": rendered_row,
        "tickers": ticker_rows,
    }


def _blocks_from_result(
    result: PackedTextResult,
    source_kind: str,
    source_ordinal: int,
) -> Iterable[NewsBlock]:
    table_ordinal = 0
    table_row_ordinal = 0
    for index, line in enumerate(result.intermediate_text.splitlines()):
        match = re.match(r"^\[([^\]]+)\]\s?(.*)$", line)
        kind, text = (match.group(1), match.group(2)) if match else ("text", line)
        if kind == "table_caption":
            table_ordinal += 1
            table_row_ordinal = 0
        elif kind == "table_columns" and table_ordinal == 0:
            table_ordinal = 1
        elif kind == "table_row":
            if table_ordinal == 0:
                table_ordinal = 1
            table_row_ordinal += 1
        yield NewsBlock(
            source_kind=source_kind,
            source_ordinal=source_ordinal,
            block_ordinal=index,
            block_kind=kind,
            text=text,
            table_ordinal=table_ordinal if kind.startswith("table") else 0,
            table_row_ordinal=table_row_ordinal if kind == "table_row" else 0,
        )


def _image_blocks(source_html: str, source_kind: str, source_ordinal: int, start: int) -> list[NewsBlock]:
    parser = _MediaParser()
    parser.feed(source_html)
    output: list[NewsBlock] = []
    for offset, (src, alt, title) in enumerate(parser.images):
        label = "; ".join(
            part for part in (f"alt={alt}" if alt else "", f"title={title}" if title else "", f"src={src}" if src else "") if part
        )
        output.append(
            NewsBlock(
                source_kind=source_kind,
                source_ordinal=source_ordinal,
                block_ordinal=start + offset,
                block_kind="image",
                text=f"Image: {label}",
            )
        )
    return output


def _clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_repair_mojibake(value or ""))).strip()


def _repair_mojibake(value: str) -> str:
    """Repair common UTF-8-as-Windows-1252 sequences only when they are present."""
    repaired = value
    for character in "‘’“”–—…•™©® ":
        try:
            broken = character.encode("utf-8").decode("cp1252")
        except UnicodeDecodeError:
            continue
        repaired = repaired.replace(broken, character)
    return repaired.replace("Â ", "\u00a0")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def _first_string(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")

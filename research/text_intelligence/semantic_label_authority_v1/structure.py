from __future__ import annotations

import html
import re
from dataclasses import replace

from .schema import StructuralBlock


NEWS_PROVENANCE_RE = re.compile(
    r"^(?:Source\s+\[[^\]]+\]\s+\S+|Image:\s+src=\S+)\s*$",
    re.IGNORECASE,
)
CONTACT_RE = re.compile(
    r"(?:\b(?:contact|investor relations|media relations|for more information)\b"
    r"|(?:mailto:)?[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|\b(?:tel|telephone|phone|fax)\b\s*[:.]?\s*[+()\d -]{7,})",
    re.IGNORECASE,
)
SIGNATURE_RE = re.compile(
    r"^(?:by:|signed:|/s/|name:|title:|authorized representative|"
    r"pursuant to the requirements of)\b",
    re.IGNORECASE,
)
BOILERPLATE_RE = re.compile(
    r"(?:forward-looking statements|safe harbor|no obligation to update|"
    r"not an offer to sell|solicitation of an offer|all rights reserved|"
    r"privacy policy|terms of use|click here to unsubscribe)",
    re.IGNORECASE,
)
HEADING_RE = re.compile(
    r"^(?:(?i:item\s+\d+(?:\.\d+)?|part\s+[ivx]+|exhibit\s+\d+)|"
    r"[A-Z][A-Z0-9 &,/()'.-]{4,})$",
)
TABLE_COLUMNS_RE = re.compile(r"^Columns:\s*(.*)$", re.IGNORECASE)
TABLE_CAPTION_RE = re.compile(r"^Table:\s*(.*)$", re.IGNORECASE)
TABLE_SCALE_RE = re.compile(
    r"(?:\(\s*\$\s*0{3}(?:['’]s)?\s*\)|"
    r"\b(?:dollars?|amounts?)\s+in\s+(thousands|millions|billions)\b)",
    re.IGNORECASE,
)


def normalize_source_text(text: str) -> str:
    return (
        html.unescape(str(text or ""))
        .replace("\x00", " ")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def segment_rendered_text(corpus: str, text: str) -> tuple[StructuralBlock, ...]:
    clean = normalize_source_text(text)
    blocks: list[StructuralBlock] = []
    offset = 0
    table_columns: tuple[str, ...] = ()
    table_currency = ""
    table_multiplier = 1
    seen_semantic_paragraphs: set[str] = set()

    for raw_line in clean.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        start = offset
        end = offset + len(line)
        offset += len(raw_line)
        if not stripped:
            blocks.append(StructuralBlock("blank", line, start, end, False, "empty"))
            continue

        kind = "prose"
        semantic = True
        reason = ""
        columns = table_columns
        currency = table_currency
        multiplier = table_multiplier

        column_match = TABLE_COLUMNS_RE.match(stripped)
        caption_match = TABLE_CAPTION_RE.match(stripped)
        if corpus == "news" and NEWS_PROVENANCE_RE.match(stripped):
            kind, semantic, reason = "renderer_provenance", False, "renderer provenance"
        elif column_match:
            table_columns = tuple(
                value.strip() for value in column_match.group(1).split(";") if value.strip()
            )
            columns = table_columns
            kind = "table_columns"
        elif caption_match:
            table_columns = ()
            columns = ()
            kind = "table_caption"
        elif CONTACT_RE.search(stripped):
            kind, semantic, reason = "contact", False, "contact metadata"
        elif SIGNATURE_RE.search(stripped):
            kind, semantic, reason = "signature", False, "signature or attestation"
        elif BOILERPLATE_RE.search(stripped):
            kind, semantic, reason = "boilerplate", False, "legal or distribution boilerplate"
        elif table_columns and ("=" in stripped or " | " in stripped):
            kind = "table_row"
        elif HEADING_RE.match(stripped) and len(stripped.split()) <= 16:
            kind = "heading"

        scale_match = TABLE_SCALE_RE.search(stripped)
        if scale_match:
            unit = (scale_match.group(1) or "thousands").lower()
            table_multiplier = {
                "thousands": 1_000,
                "millions": 1_000_000,
                "billions": 1_000_000_000,
            }[unit]
            table_currency = "USD"
            currency = table_currency
            multiplier = table_multiplier

        compact = re.sub(r"^(?:teaser|summary|body)\s*:\s*", "", stripped, flags=re.IGNORECASE)
        compact = re.sub(r"\s+", " ", compact).casefold()
        if semantic and kind == "prose" and len(compact) >= 80:
            if compact in seen_semantic_paragraphs:
                semantic, reason, kind = False, "duplicate rendered paragraph", "duplicate"
            else:
                seen_semantic_paragraphs.add(compact)

        blocks.append(
            StructuralBlock(
                kind=kind,
                text=line,
                start=start,
                end=end,
                semantic=semantic,
                reason=reason,
                table_columns=columns,
                table_currency=currency,
                table_multiplier=multiplier,
            )
        )
    # Rendered News teasers are often a truncated prefix of the full provider
    # body. Suppress that duplicate only when a later retained block begins with
    # the same normalized payload; standalone teasers remain semantic.
    if corpus == "news":
        for index, block in enumerate(blocks):
            payload = re.sub(r"^teaser\s*:\s*", "", block.text.strip(), flags=re.IGNORECASE)
            compact = re.sub(r"\s+", " ", payload).casefold()
            if not block.semantic or not block.text.lstrip().casefold().startswith("teaser:") or len(compact) < 40:
                continue
            if any(
                later.semantic
                and re.sub(r"\s+", " ", later.text.strip()).casefold().startswith(compact)
                for later in blocks[index + 1 :]
            ):
                blocks[index] = replace(
                    block,
                    kind="duplicate_teaser",
                    semantic=False,
                    reason="teaser duplicates provider-body prefix",
                )
    return tuple(blocks)


def semantic_text(text: str, blocks: tuple[StructuralBlock, ...]) -> str:
    clean = normalize_source_text(text)
    retained = [
        clean[block.start : block.end]
        for block in blocks
        if block.semantic and block.kind != "blank"
    ]
    return "\n".join(value for value in retained if value.strip())


def block_for_offset(
    blocks: tuple[StructuralBlock, ...],
    start: int,
) -> StructuralBlock | None:
    for block in blocks:
        if block.start <= start <= block.end:
            return block
    return None

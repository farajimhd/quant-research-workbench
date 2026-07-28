from __future__ import annotations

import hashlib
import re

from research.text_intelligence.semantic_label_authority_v1.structure import (
    normalize_source_text,
    segment_rendered_text,
)

from .schema import SEC_EXTRACTOR_VERSION, RelevantTextUnit


ADMIN_HEADING_RE = re.compile(
    r"^(?:signatures?|power of attorney|definitions?|exhibit index|"
    r"authorized representative|consent of independent|conference call|"
    r"about\s+|forward-looking|non-gaap|contacts?)",
    re.IGNORECASE,
)
RELEVANT_RE = re.compile(
    r"\b(?:offering|placement|warrant|financing|merger|acquisition|"
    r"guidance|revenue|earnings|clinical|fda|agreement|contract|"
    r"bankruptcy|going concern|material weakness|restat|dividend|"
    r"repurchase|reverse stock split|workforce reduction|investigation|"
    r"lawsuit|settlement|purchase order)\b",
    re.IGNORECASE,
)
def extract_sec_units(
    *,
    source_id: str,
    title: str,
    text: str,
    ticker: str,
    metadata: dict,
) -> tuple[RelevantTextUnit, ...]:
    clean = normalize_source_text(text)
    blocks = segment_rendered_text("sec", clean)
    document_role = str(metadata.get("document_role") or "")
    sections: list[tuple[str, list[object]]] = []
    heading = ""
    active: list[object] = []
    for block in blocks:
        stripped = block.text.strip()
        if _is_effective_heading(stripped, block.kind):
            if active:
                sections.append((heading, active))
            active = []
            heading = stripped
            continue
        if not block.semantic or block.kind == "blank":
            continue
        if block.kind in {"signature", "contact", "boilerplate"}:
            continue
        active.append(block)
    if active:
        sections.append((heading, active))

    # Preserve deterministic document order. Cap only the number of derived
    # evidence units; the canonical rendered filing is never truncated.
    output: list[RelevantTextUnit] = []
    seen: set[str] = set()
    for section_heading, section_blocks in sections:
        if section_heading and ADMIN_HEADING_RE.search(section_heading):
            continue
        raw = "\n".join(block.text.strip() for block in section_blocks)
        if not RELEVANT_RE.search(f"{section_heading}\n{raw}"):
            continue
        compact = re.sub(r"\s+", " ", raw).strip()
        fingerprint = compact.casefold()
        if fingerprint in seen or len(compact) < 20:
            continue
        seen.add(fingerprint)
        ordinal = len(output) + 1
        digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16]
        output.append(
            RelevantTextUnit(
                corpus="sec",
                source_id=source_id,
                unit_id=f"{source_id}:sec:{ordinal}:{digest}",
                ordinal=ordinal,
                role="relevant_filing_section",
                text=compact,
                start=section_blocks[0].start,
                end=section_blocks[-1].end,
                tickers=(ticker,) if ticker else (),
                shared_context=False,
                heading=section_heading,
                document_id=source_id,
                document_role=document_role,
                extractor_version=SEC_EXTRACTOR_VERSION,
                quality_flags=(
                    ("missing_point_in_time_ticker",) if not ticker else ()
                ),
            )
        )
        if len(output) >= 24:
            break
    return tuple(output)


def _is_effective_heading(text: str, block_kind: str = "") -> bool:
    """Reject sentence-like false headings from the legacy broad detector."""
    if not text:
        return False
    if re.match(r"^(?:item\s+\d|part\s+[ivx]+|exhibit\s+\d)", text, re.I):
        return True
    words = text.split()
    if (
        len(words) <= 16
        and text == text.upper()
        and not text.endswith((".", ";", ":"))
    ):
        return True
    title_case = sum(
        word[:1].isupper()
        for word in words
        if any(character.isalpha() for character in word)
    )
    alpha_words = sum(
        any(character.isalpha() for character in word) for word in words
    )
    return (
        block_kind == "heading"
        or (
            1 <= len(words) <= 10
            and alpha_words > 0
            and title_case / alpha_words >= 0.75
            and not text.endswith((".", ";", ":"))
            and "=" not in text
        )
    )

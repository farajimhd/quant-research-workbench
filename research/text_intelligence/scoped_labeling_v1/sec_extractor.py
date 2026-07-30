from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from pipelines.sec.edgar.sec_taxonomy import semantic_label
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
    r"lawsuit|settlement|purchase order|employee (?:stock|share) purchase "
    r"plan|preferred stock)\b",
    re.IGNORECASE,
)

MAX_SEC_EVIDENCE_CHARS = 32_000
MAX_SEC_EVIDENCE_UNITS = 24


def sec_document_labeling_eligible(metadata: dict) -> bool:
    """Use the approved SEC taxonomy to exclude non-narrative data products."""
    document_type = str(
        metadata.get("document_type")
        or metadata.get("form_type")
        or ""
    )
    description = " ".join(
        str(metadata.get(key) or "")
        for key in ("description", "document_name", "title")
    )
    return semantic_label(
        document_type,
        description,
        scope="document",
    ).embedding_enabled


def extract_sec_units(
    *,
    source_id: str,
    title: str,
    text: str,
    ticker: str,
    metadata: dict,
) -> tuple[RelevantTextUnit, ...]:
    # Asset-level XML, XBRL, fund datasets, certifications, and other
    # structured/administrative products remain in the canonical SEC authority.
    # They are not narrative documents and must not be expanded into millions
    # of per-value semantic spans.
    if not sec_document_labeling_eligible(metadata):
        return ()
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

    # Preserve deterministic document order while selecting bounded narrative
    # evidence. The complete canonical filing remains unchanged and addressable
    # through source identity, evidence offsets, and hashes.
    output: list[RelevantTextUnit] = []
    seen: set[str] = set()
    for section_heading, section_blocks in sections:
        if section_heading and ADMIN_HEADING_RE.search(section_heading):
            continue
        heading_relevant = bool(RELEVANT_RE.search(section_heading))
        matching = {
            index
            for index, block in enumerate(section_blocks)
            if RELEVANT_RE.search(block.text)
        }
        if not heading_relevant and not matching:
            continue
        selected = (
            set(range(len(section_blocks)))
            if heading_relevant
            else {
                neighbor
                for index in matching
                for neighbor in (index - 1, index, index + 1)
                if 0 <= neighbor < len(section_blocks)
            }
        )
        for compact, start, end in _bounded_section_evidence(
            section_heading,
            section_blocks,
            selected,
        ):
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
                    semantic_text=compact,
                    start=start,
                    end=end,
                    tickers=(ticker,) if ticker else (),
                    shared_context=False,
                    event_id=f"{source_id}:event:{digest}",
                    event_tickers=(ticker,) if ticker else (),
                    issuer_role=(
                        "primary_filer" if ticker else "unmapped_filer"
                    ),
                    evidence_scope="ticker_specific",
                    trigger_candidate=True,
                    heading=section_heading,
                    document_id=source_id,
                    document_role=document_role,
                    extractor_version=SEC_EXTRACTOR_VERSION,
                    quality_flags=(
                        ("missing_point_in_time_ticker",) if not ticker else ()
                    ),
                )
            )
            if len(output) >= MAX_SEC_EVIDENCE_UNITS:
                output[-1] = replace(
                    output[-1],
                    quality_flags=tuple(dict.fromkeys((
                        *output[-1].quality_flags,
                        "evidence_unit_limit_reached",
                    ))),
                )
                return tuple(output)
    return tuple(output)


def _bounded_section_evidence(
    heading: str,
    blocks: list[object],
    selected: set[int],
):
    """Yield complete selected evidence in structurally bounded units."""
    heading_text = re.sub(r"\s+", " ", heading).strip()
    heading_prefix = f"{heading_text}\n" if heading_text else ""
    payload_limit = max(1_000, MAX_SEC_EVIDENCE_CHARS - len(heading_prefix))
    current: list[str] = []
    current_chars = 0
    current_start = 0
    current_end = 0

    def flush():
        nonlocal current, current_chars, current_start, current_end
        if not current:
            return None
        value = re.sub(
            r"\s+",
            " ",
            f"{heading_prefix}{' '.join(current)}",
        ).strip()
        result = (value, current_start, current_end)
        current = []
        current_chars = 0
        current_start = 0
        current_end = 0
        return result

    previous_index: int | None = None
    for index, block in enumerate(blocks):
        if index not in selected:
            pending = flush()
            if pending:
                yield pending
            previous_index = None
            continue
        if previous_index is not None and index != previous_index + 1:
            pending = flush()
            if pending:
                yield pending
        previous_index = index
        for local_start, local_end, fragment in _bounded_fragments(
            block.text,
            payload_limit,
        ):
            fragment_start = block.start + local_start
            fragment_end = block.start + local_end
            extra = len(fragment) + (1 if current else 0)
            if current and current_chars + extra > payload_limit:
                pending = flush()
                if pending:
                    yield pending
            if not current:
                current_start = fragment_start
            current.append(fragment)
            current_chars += len(fragment) + (1 if len(current) > 1 else 0)
            current_end = fragment_end
    pending = flush()
    if pending:
        yield pending


def _bounded_fragments(text: str, limit: int):
    """Split without dropping source text, preferring natural boundaries."""
    cursor = 0
    length = len(text)
    while cursor < length:
        hard_end = min(length, cursor + limit)
        end = hard_end
        if hard_end < length:
            window = text[cursor:hard_end]
            candidates = (
                window.rfind("\n"),
                window.rfind(". "),
                window.rfind("; "),
                window.rfind(" "),
            )
            boundary = max(candidates)
            if boundary >= limit // 2:
                end = cursor + boundary + 1
        raw = text[cursor:end]
        compact = re.sub(r"\s+", " ", raw).strip()
        if compact:
            yield cursor, end, compact
        cursor = end


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

from __future__ import annotations

import re
from collections import Counter

from research.text_intelligence.candidate_inventory_v1.normalize import (
    STOP_WORDS,
    candidate_ngrams,
    tokens,
)

from .concepts import (
    CONCEPT_BY_PHRASE,
    CONCEPT_RULES,
    MODALITY_PATTERNS,
    ROLE_PATTERNS,
    TIME_PATTERNS,
)
from .extract import extract_spans
from .schema import (
    LABEL_AUTHORITY_VERSION,
    CandidateEvidence,
    CanonicalLabel,
    LabelEvidence,
    SemanticDocument,
    SemanticResult,
    SemanticSpan,
)
from .structure import normalize_source_text, segment_rendered_text, semantic_text


WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")


def label_document(
    document: SemanticDocument,
    *,
    include_discovery_evidence: bool = True,
) -> SemanticResult:
    if document.corpus not in {"news", "sec"}:
        raise ValueError(f"unsupported corpus {document.corpus!r}")
    clean = normalize_source_text(document.text)
    blocks = segment_rendered_text(document.corpus, clean)
    retained = semantic_text(clean, blocks)
    spans = extract_spans(document, blocks)
    normalized = normalize_semantic_text(clean, blocks, spans)
    labels = concept_labels(clean, blocks)
    content_role = classify_content_role(document, retained, labels)
    origin = classify_origin(document, content_role)
    sentiment_score = sum(
        next(
            (
                rule.weight
                for rule in CONCEPT_RULES
                if rule.family == label.family and rule.subtype == label.subtype
            ),
            0.0,
        )
        for label in labels
    )
    sentiment = sentiment_label(sentiment_score, labels)
    modality = dominant_label_value([label.modality for label in labels]) or detect_modality(retained)
    orientation = dominant_label_value([label.time_orientation for label in labels]) or detect_time_orientation(retained)
    keywords = clean_keywords(normalized) if include_discovery_evidence else ()
    candidates = document_candidates(normalized) if include_discovery_evidence else ()
    flags = quality_flags(document, blocks, spans, labels)
    if content_role in {"market_roundup", "mover_recap", "why_moving_followup"}:
        flags = tuple(value for value in flags if value != "no_supported_canonical_event")
    return SemanticResult(
        authority_version=LABEL_AUTHORITY_VERSION,
        source_id=document.source_id,
        corpus=document.corpus,
        blocks=blocks,
        spans=spans,
        normalized_semantic_text=normalized,
        labels=labels,
        content_role=content_role,
        origin=origin,
        sentiment=sentiment,
        sentiment_score=round(sentiment_score, 4),
        modality=modality,
        time_orientation=orientation,
        keywords=keywords,
        candidates=candidates,
        quality_flags=flags,
    )


def normalize_semantic_text(
    text: str,
    blocks: tuple,
    spans: tuple[SemanticSpan, ...],
) -> str:
    retained_ranges = [(block.start, block.end) for block in blocks if block.semantic and block.kind != "blank"]
    parts: list[str] = []
    for start, end in retained_ranges:
        value = text[start:end]
        local = [
            span for span in spans
            if span.start >= start and span.end <= end
        ]
        for span in sorted(local, key=lambda item: item.start, reverse=True):
            relative_start = span.start - start
            relative_end = span.end - start
            placeholder = f"<{span.span_type}_{span.subtype}>"
            value = value[:relative_start] + placeholder + value[relative_end:]
        parts.append(value.strip())
    combined = "\n".join(value for value in parts if value)
    combined = re.sub(r"(?im)^(?:Title|Teaser|Summary|Body):\s*", "", combined)
    combined = re.sub(r"[ \t]+", " ", combined)
    combined = re.sub(r"\n{3,}", "\n\n", combined)
    return combined.strip()


def concept_labels(text: str, blocks: tuple) -> tuple[CanonicalLabel, ...]:
    semantic_ranges = [(block.start, block.end) for block in blocks if block.semantic]
    lowered = text.casefold()
    output: list[CanonicalLabel] = []
    for rule in CONCEPT_RULES:
        evidence: list[LabelEvidence] = []
        for phrase in sorted(rule.phrases, key=len, reverse=True):
            for match in re.finditer(rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)", lowered):
                if not any(start <= match.start() and match.end() <= end for start, end in semantic_ranges):
                    continue
                if _excluded_concept_match(rule, text, match.start(), match.end()):
                    continue
                evidence.append(LabelEvidence("text", text[match.start():match.end()], match.start(), match.end()))
        for pattern in rule.patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if not any(start <= match.start() and match.end() <= end for start, end in semantic_ranges):
                    continue
                if _excluded_concept_match(rule, text, match.start(), match.end()):
                    continue
                evidence.append(LabelEvidence("text", match.group(0), match.start(), match.end()))
        evidence = list({
            (item.start, item.end, item.text): item
            for item in evidence
        }.values())
        if not evidence:
            continue
        # One canonical event concept per document; all exact occurrences remain evidence.
        output.append(
            CanonicalLabel(
                family=rule.family,
                subtype=rule.subtype,
                direction=rule.direction,
                modality=detect_modality(" ".join(item.text for item in evidence), rule.modality),
                time_orientation=rule.time_orientation,
                confidence=min(0.99, 0.82 + 0.03 * len(evidence)),
                evidence=tuple(evidence[:8]),
            )
        )
    return tuple(sorted(output, key=lambda item: (item.family, item.subtype)))


def _excluded_concept_match(
    rule,
    text: str,
    start: int,
    end: int,
) -> bool:
    if not rule.exclude_patterns:
        return False
    context = text[max(0, start - 120) : min(len(text), end + 120)]
    return any(
        re.search(pattern, context, re.IGNORECASE)
        for pattern in rule.exclude_patterns
    )


def classify_content_role(
    document: SemanticDocument,
    text: str,
    labels: tuple[CanonicalLabel, ...],
) -> str:
    title = document.title or text[:500]
    for role, pattern in ROLE_PATTERNS:
        if pattern.search(title):
            return role
    if document.corpus == "sec" and any(
        label.family not in {"regulatory"} and label.subtype != "power_of_attorney"
        for label in labels
    ):
        return "primary_event"
    if document.corpus == "sec":
        return "regulatory_primary"
    if any(label.family == "analyst_action" for label in labels):
        return "analyst_event"
    if any(label.family in {"regulatory", "listing_market_structure"} for label in labels):
        return "regulatory_event"
    if labels:
        return "primary_event"
    return "editorial_analysis"


def classify_origin(document: SemanticDocument, content_role: str) -> str:
    if document.corpus == "sec":
        return "regulatory_primary"
    if content_role == "analyst_event":
        return "analyst_research"
    if content_role in {"market_roundup", "mover_recap", "why_moving_followup"}:
        return "editorial_aggregation"
    author = str(document.metadata.get("author") or "").casefold()
    if "insights" in author or any(str(tag).casefold().startswith("bzi-") for tag in document.metadata.get("provider_tags") or []):
        return "automated_summary"
    return "editorial_original"


def sentiment_label(
    score: float,
    labels: tuple[CanonicalLabel, ...],
) -> str:
    directions = {label.direction for label in labels if label.direction != "neutral"}
    if "positive" in directions and "negative" in directions and abs(score) < 0.75:
        return "mixed"
    if score >= 0.5:
        return "positive"
    if score <= -0.5:
        return "negative"
    return "neutral"


def detect_modality(text: str, default: str = "confirmed") -> str:
    matches = [name for name, pattern in MODALITY_PATTERNS if pattern.search(text)]
    return "mixed" if len(set(matches)) > 1 else (matches[0] if matches else default)


def detect_time_orientation(text: str) -> str:
    matches = [name for name, pattern in TIME_PATTERNS if pattern.search(text)]
    return "mixed" if len(set(matches)) > 1 else (matches[0] if matches else "current")


def dominant_label_value(values: list[str]) -> str:
    filtered = [value for value in values if value]
    if not filtered:
        return ""
    counts = Counter(filtered)
    if len(counts) > 1 and counts.most_common(2)[0][1] == counts.most_common(2)[1][1]:
        return "mixed"
    return counts.most_common(1)[0][0]


def clean_keywords(normalized: str) -> tuple[str, ...]:
    lexical = re.sub(r"<[a-z_]+>", " ", normalized)
    counts = Counter(value.casefold() for value in WORD_RE.findall(lexical))
    values = [
        word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if word not in STOP_WORDS and len(word) >= 3
    ]
    return tuple(values[:100])


def document_candidates(normalized: str) -> tuple[CandidateEvidence, ...]:
    counts = Counter(
        phrase for phrase, _ in candidate_ngrams(normalized, min_ngram=2, max_ngram=6)
        if "<identifier>" not in phrase and "<url>" not in phrase
    )
    candidates = [
        CandidateEvidence(
            phrase,
            count,
            len(phrase.split()),
            CONCEPT_BY_PHRASE.get(phrase.casefold(), ""),
        )
        for phrase, count in sorted(
            counts.items(),
            key=lambda item: (
                item[0].casefold() not in CONCEPT_BY_PHRASE,
                -item[1],
                -len(item[0].split()),
                item[0],
            ),
        )
    ]
    # Keep only the longest representative when a same-count candidate is wholly
    # contained in a stronger phrase. This is audit evidence, not a label catalog.
    retained: list[CandidateEvidence] = []
    for candidate in candidates:
        if any(
            candidate.count == existing.count
            and f" {candidate.phrase} " in f" {existing.phrase} "
            for existing in retained
        ):
            continue
        retained.append(candidate)
        if len(retained) >= 150:
            break
    return tuple(retained)


def quality_flags(document, blocks, spans, labels) -> tuple[str, ...]:
    flags: list[str] = []
    if len(document.text.strip()) < 200:
        flags.append("insufficient_text")
    if any(block.kind == "duplicate" for block in blocks):
        flags.append("duplicate_rendered_content_removed")
    if sum(1 for block in blocks if not block.semantic) > max(8, len(blocks) // 2):
        flags.append("boilerplate_or_metadata_dominant")
    if any(span.subtype == "table_quantity" and not span.unit for span in spans):
        flags.append("table_quantity_unit_unresolved")
    if not labels:
        flags.append("no_supported_canonical_event")
    if (
        document.corpus == "sec"
        and str(document.metadata.get("text_kind") or "") == "prospectus"
        and str(document.metadata.get("document_type") or "").upper().startswith("EX-25")
    ):
        flags.append("source_taxonomy_mismatch_prospectus_vs_ex25")
    return tuple(flags)

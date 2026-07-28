from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


LABEL_AUTHORITY_VERSION = "text_semantic_label_authority_v2"
STRUCTURE_VERSION = "rendered_text_structure_v1"
SPAN_VERSION = "typed_semantic_spans_v1"
CONCEPT_VERSION = "deterministic_event_concepts_v2"


@dataclass(frozen=True, slots=True)
class SemanticDocument:
    corpus: str
    source_id: str
    timestamp: str
    title: str
    text: str
    entity_terms: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StructuralBlock:
    kind: str
    text: str
    start: int
    end: int
    semantic: bool
    reason: str = ""
    table_columns: tuple[str, ...] = ()
    table_currency: str = ""
    table_multiplier: int = 1


@dataclass(frozen=True, slots=True)
class SemanticSpan:
    span_type: str
    subtype: str
    raw: str
    normalized: str
    start: int
    end: int
    context: str
    unit: str = ""
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LabelEvidence:
    source: str
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class CanonicalLabel:
    family: str
    subtype: str
    direction: str
    modality: str
    time_orientation: str
    confidence: float
    evidence: tuple[LabelEvidence, ...]


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    phrase: str
    count: int
    token_count: int
    seed_concept: str = ""


@dataclass(frozen=True, slots=True)
class SemanticResult:
    authority_version: str
    source_id: str
    corpus: str
    blocks: tuple[StructuralBlock, ...]
    spans: tuple[SemanticSpan, ...]
    normalized_semantic_text: str
    labels: tuple[CanonicalLabel, ...]
    content_role: str
    origin: str
    sentiment: str
    sentiment_score: float
    modality: str
    time_orientation: str
    keywords: tuple[str, ...]
    candidates: tuple[CandidateEvidence, ...]
    quality_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

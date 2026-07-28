from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CLASSIFICATION_AUTHORITY_VERSION = "text_classification_authority_v3"


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    authority_version: str
    corpus: str
    source_id: str
    source_type: str
    source_subtype: str
    source_origin: str
    content_role: str
    issuer_relationship: str
    scope: str
    event_concepts: tuple[str, ...]
    semantic_direction: str
    semantic_score: float
    modality: str
    time_orientation: str
    forecast_trigger_eligible: bool
    reaction_evaluation_eligible: bool
    prior_primary_context_eligible: bool
    episode_followup_eligible: bool
    confidence: float
    evidence: tuple[str, ...]
    quality_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

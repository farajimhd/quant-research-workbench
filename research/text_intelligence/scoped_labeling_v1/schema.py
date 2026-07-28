from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SCOPED_LABELING_VERSION = "scoped_text_labeling_v2"
NEWS_EXTRACTOR_VERSION = "news_relevant_text_v2"
SEC_EXTRACTOR_VERSION = "sec_relevant_section_v1"


@dataclass(frozen=True, slots=True)
class ObservedReaction:
    direction: str = ""
    move_pct: float | None = None
    resulting_price: float | None = None
    market_session: str = ""
    timing: str = ""
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class RelevantTextUnit:
    corpus: str
    source_id: str
    unit_id: str
    ordinal: int
    role: str
    text: str
    start: int
    end: int
    tickers: tuple[str, ...]
    shared_context: bool
    heading: str = ""
    document_id: str = ""
    document_role: str = ""
    observed_reaction: ObservedReaction = ObservedReaction()
    reported_catalyst: str = ""
    extractor_version: str = ""
    quality_flags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScopedLabel:
    corpus: str
    source_id: str
    unit_id: str
    ticker: str
    unit_role: str
    classification: dict[str, Any]
    semantic: dict[str, Any]
    observed_reaction: ObservedReaction
    reported_catalyst: str
    forecast_trigger_eligible: bool
    reaction_evaluation_eligible: bool
    issuer_history_context_eligible: bool
    version: str = SCOPED_LABELING_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

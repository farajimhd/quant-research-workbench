from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable


TAXONOMY_SCHEMA_VERSION = 3
SUPPORTED_TAXONOMY_SCHEMA_VERSIONS = {1, 2, TAXONOMY_SCHEMA_VERSION}


class IndicatorType(StrEnum):
    TECHNICAL = "technical"
    QMD = "qmd"
    FUNDAMENTAL = "fundamental"
    REFERENCE = "reference"
    MODEL = "model"


class SignalDomain(StrEnum):
    MARKET = "market"
    NEWS = "news"
    SEC = "sec"
    MODEL = "model"


class InputBasis(StrEnum):
    EVENT_NATIVE = "event_native"
    BAR_DERIVED = "bar_derived"
    INDICATOR_DERIVED = "indicator_derived"
    DOCUMENT_EVENT = "document_event"
    REFERENCE_SNAPSHOT = "reference_snapshot"
    MODEL_OUTPUT = "model_output"


class EvaluationMode(StrEnum):
    DEVELOPING = "developing"
    CLOSED_ONLY = "closed_only"
    POINT_IN_TIME = "point_in_time"


class UpdateTrigger(StrEnum):
    MARKET_EVENT = "market_event"
    BAR_CLOSE = "bar_close"
    INDICATOR_UPDATE = "indicator_update"
    SOURCE_EVENT = "source_event"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class PublicationCadence(StrEnum):
    EVERY_EVENT = "every_event"
    INTERVAL = "interval"
    BAR_CLOSE = "bar_close"
    ON_CHANGE = "on_change"
    SOURCE_EVENT = "source_event"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class ClockContract:
    """Explicitly separates calculation semantics from consumer publication."""

    input_basis: InputBasis
    evaluation_mode: EvaluationMode
    update_trigger: UpdateTrigger
    publication_cadence: PublicationCadence
    calculation_window: str | None = None
    publication_interval_ms: int | None = None

    def __post_init__(self) -> None:
        if self.publication_cadence == PublicationCadence.INTERVAL:
            if self.publication_interval_ms is None or self.publication_interval_ms <= 0:
                raise ValueError("Interval publication requires publication_interval_ms > 0")
        elif self.publication_interval_ms is not None:
            raise ValueError("publication_interval_ms is valid only for interval publication")
        if self.update_trigger == UpdateTrigger.BAR_CLOSE and not self.calculation_window:
            raise ValueError("Bar-close evaluation requires a calculation_window")

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IndicatorDefinition:
    indicator_id: str
    label: str
    indicator_type: IndicatorType
    producer: str
    outputs: tuple[str, ...]
    clock: ClockContract
    description: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.indicator_id, "indicator_id")
        if not self.producer.strip():
            raise ValueError("Indicator producer is required")
        if not self.outputs or any(not output.strip() for output in self.outputs):
            raise ValueError("Indicator outputs must contain at least one named field")

    def payload(self) -> dict[str, Any]:
        return _enum_payload(asdict(self))


@dataclass(frozen=True, slots=True)
class SignalDefinition:
    signal_id: str
    label: str
    domain: SignalDomain
    producer: str
    clock: ClockContract
    description: str = ""
    score_required: bool = True
    rank_score_required: bool = True

    def __post_init__(self) -> None:
        _require_identifier(self.signal_id, "signal_id")
        if not self.producer.strip():
            raise ValueError("Signal producer is required")
        if not self.score_required:
            raise ValueError("Every ranked signal must require a score")
        if not self.rank_score_required:
            raise ValueError("Every scanner signal must require an authority rank score")

    def payload(self) -> dict[str, Any]:
        return _enum_payload(asdict(self))


@dataclass(frozen=True, slots=True)
class StrategyInputRef:
    key: str
    required: bool = True
    timeframe: str = ""
    role: str = "context"
    evaluation_mode: str = "closed_only"
    maximum_age_ms: int | None = None
    weight: float = 1.0
    minimum_score: float | None = None
    minimum_confidence: float | None = None
    parameters: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        _require_identifier(self.key, "strategy input key")
        if self.role not in {"trigger", "confirmation", "veto", "sizing", "exit", "context"}:
            raise ValueError(f"Unsupported strategy input role: {self.role}")
        if self.evaluation_mode not in {item.value for item in EvaluationMode}:
            raise ValueError(f"Unsupported strategy input evaluation mode: {self.evaluation_mode}")
        if self.maximum_age_ms is not None and self.maximum_age_ms <= 0:
            raise ValueError("Strategy input maximum_age_ms must be positive")
        if self.weight < 0:
            raise ValueError("Strategy input weight cannot be negative")
        if self.minimum_score is not None and not -1 <= self.minimum_score <= 1:
            raise ValueError("Strategy input minimum_score must be between -1 and 1")
        if self.minimum_confidence is not None and not 0 <= self.minimum_confidence <= 1:
            raise ValueError("Strategy input minimum_confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class StrategyPresentation:
    """Strategy-agnostic chart policy; strategy logic emits decisions, not pixels."""

    show_entries: bool = True
    show_adds: bool = True
    show_reductions: bool = True
    show_exits: bool = True
    show_holds: bool = False
    show_waits: bool = False
    show_invalidation: bool = True
    show_confidence: bool = True
    label: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrategyTaxonomy:
    indicators: tuple[StrategyInputRef, ...] = ()
    signals: tuple[StrategyInputRef, ...] = ()
    allow_developing_inputs: bool = False
    presentation: StrategyPresentation = field(default_factory=StrategyPresentation)
    schema_version: int = TAXONOMY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_TAXONOMY_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported strategy taxonomy schema version: {self.schema_version}")
        _reject_duplicate_inputs(self.indicators, "indicator")
        _reject_duplicate_inputs(self.signals, "signal")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": TAXONOMY_SCHEMA_VERSION,
            "indicators": [_enum_payload(asdict(item)) for item in self.indicators],
            "signals": [_enum_payload(asdict(item)) for item in self.signals],
            "allow_developing_inputs": self.allow_developing_inputs,
            "presentation": self.presentation.payload(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "StrategyTaxonomy":
        value = dict(payload or {})
        presentation = dict(value.get("presentation") or {})
        return cls(
            schema_version=TAXONOMY_SCHEMA_VERSION,
            indicators=_input_refs(value.get("indicators")),
            signals=_input_refs(value.get("signals")),
            allow_developing_inputs=bool(value.get("allow_developing_inputs", False)),
            presentation=StrategyPresentation(
                show_entries=bool(presentation.get("show_entries", True)),
                show_adds=bool(presentation.get("show_adds", True)),
                show_reductions=bool(presentation.get("show_reductions", True)),
                show_exits=bool(presentation.get("show_exits", True)),
                show_holds=bool(presentation.get("show_holds", False)),
                show_waits=bool(presentation.get("show_waits", False)),
                show_invalidation=bool(presentation.get("show_invalidation", True)),
                show_confidence=bool(presentation.get("show_confidence", True)),
                label=str(presentation.get("label") or ""),
            ),
        )


def taxonomy_catalog_payload() -> dict[str, Any]:
    return {
        "schema_version": TAXONOMY_SCHEMA_VERSION,
        "indicator_types": [item.value for item in IndicatorType],
        "signal_domains": [item.value for item in SignalDomain],
        "input_bases": [item.value for item in InputBasis],
        "evaluation_modes": [item.value for item in EvaluationMode],
        "update_triggers": [item.value for item in UpdateTrigger],
        "publication_cadences": [item.value for item in PublicationCadence],
        "strategy_actions": ["enter_long", "add_long", "reduce_long", "take_profit", "exit", "hold", "wait"],
        "strategy_input_roles": ["trigger", "confirmation", "veto", "sizing", "exit", "context"],
    }


def _input_refs(value: Any) -> tuple[StrategyInputRef, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ValueError("Strategy taxonomy inputs must be lists")
    return tuple(
        StrategyInputRef(
            key=str(item.get("key") or "") if isinstance(item, dict) else str(item),
            required=bool(item.get("required", True)) if isinstance(item, dict) else True,
            timeframe=str(item.get("timeframe") or "") if isinstance(item, dict) else "",
            role=str(item.get("role") or "context") if isinstance(item, dict) else "context",
            evaluation_mode=str(item.get("evaluation_mode") or "closed_only") if isinstance(item, dict) else "closed_only",
            maximum_age_ms=int(item["maximum_age_ms"]) if isinstance(item, dict) and item.get("maximum_age_ms") is not None else None,
            weight=float(item.get("weight", 1.0)) if isinstance(item, dict) else 1.0,
            minimum_score=float(item["minimum_score"]) if isinstance(item, dict) and item.get("minimum_score") is not None else None,
            minimum_confidence=float(item["minimum_confidence"]) if isinstance(item, dict) and item.get("minimum_confidence") is not None else None,
            parameters=dict(item.get("parameters") or {}) if isinstance(item, dict) else {},
        )
        for item in value
    )


def _reject_duplicate_inputs(values: Iterable[StrategyInputRef], label: str) -> None:
    keys = [item.key for item in values]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Strategy taxonomy contains duplicate {label} inputs")


def _require_identifier(value: str, label: str) -> None:
    normalized = value.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError(f"{label} must be a non-empty identifier without whitespace")


def _enum_payload(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_payload(item) for item in value]
    return value

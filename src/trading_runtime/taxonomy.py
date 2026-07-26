from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable


TAXONOMY_SCHEMA_VERSION = 1


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

    def __post_init__(self) -> None:
        _require_identifier(self.key, "strategy input key")


@dataclass(frozen=True, slots=True)
class StrategyPresentation:
    """Strategy-agnostic chart policy; strategy logic emits decisions, not pixels."""

    show_entries: bool = True
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
    evaluation_trigger: str = "signal_event"
    presentation: StrategyPresentation = field(default_factory=StrategyPresentation)
    schema_version: int = TAXONOMY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TAXONOMY_SCHEMA_VERSION:
            raise ValueError(f"Unsupported strategy taxonomy schema version: {self.schema_version}")
        if self.evaluation_trigger not in {"market_event", "bar_close", "signal_event", "manual"}:
            raise ValueError(f"Unsupported strategy evaluation trigger: {self.evaluation_trigger}")
        _reject_duplicate_inputs(self.indicators, "indicator")
        _reject_duplicate_inputs(self.signals, "signal")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "indicators": [asdict(item) for item in self.indicators],
            "signals": [asdict(item) for item in self.signals],
            "allow_developing_inputs": self.allow_developing_inputs,
            "evaluation_trigger": self.evaluation_trigger,
            "presentation": self.presentation.payload(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "StrategyTaxonomy":
        value = dict(payload or {})
        presentation = dict(value.get("presentation") or {})
        return cls(
            schema_version=int(value.get("schema_version") or TAXONOMY_SCHEMA_VERSION),
            indicators=_input_refs(value.get("indicators")),
            signals=_input_refs(value.get("signals")),
            allow_developing_inputs=bool(value.get("allow_developing_inputs", False)),
            evaluation_trigger=str(value.get("evaluation_trigger") or "signal_event"),
            presentation=StrategyPresentation(
                show_entries=bool(presentation.get("show_entries", True)),
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
        "strategy_actions": ["enter_long", "enter_short", "exit", "hold", "wait"],
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

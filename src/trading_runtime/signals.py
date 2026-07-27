from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from src.trading_runtime.taxonomy import (
    ClockContract,
    EvaluationMode,
    InputBasis,
    PublicationCadence,
    SignalDomain,
    UpdateTrigger,
)

SignalDirection = Literal["bullish", "bearish", "neutral"]
SignalState = Literal["triggered", "updated", "resolved", "expired"]
StrategyAction = Literal[
    "enter_long",
    "add_long",
    "reduce_long",
    "take_profit",
    "exit",
    "enter_short",
    "add_short",
    "reduce_short",
    "cover",
    "hold",
    "wait",
]


@dataclass(frozen=True, slots=True)
class MarketSignal:
    """Versioned QMD/enrichment signal consumed identically in live and replay."""

    signal_id: str
    event_id: str
    signal_key: str
    schema_version: int
    signal_version: int
    engine_version: str
    producer: str
    ticker: str
    working_timeframe: str
    observed_at: datetime
    effective_at: datetime
    state: SignalState
    direction: SignalDirection
    score: float
    rank_score: float
    confidence: float
    trigger_reason: str
    reference_price: float
    confirmation_timeframe: str | None = None
    resolution_reason: str = ""
    invalidation_price: float | None = None
    expires_at: datetime | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    domain: SignalDomain = SignalDomain.MARKET
    clock: ClockContract | None = None

    def __post_init__(self) -> None:
        if self.domain != SignalDomain.MARKET:
            raise ValueError("MarketSignal domain must be market")
        if not -1.0 <= self.score <= 1.0:
            raise ValueError("MarketSignal score must be between -1 and 1")
        if not 0.0 <= self.rank_score <= 1.0:
            raise ValueError("MarketSignal rank_score must be between 0 and 1")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("MarketSignal confidence must be between 0 and 1")
        if self.clock is None:
            object.__setattr__(
                self,
                "clock",
                ClockContract(
                    input_basis=InputBasis.BAR_DERIVED,
                    calculation_window=self.working_timeframe,
                    evaluation_mode=EvaluationMode.CLOSED_ONLY,
                    update_trigger=UpdateTrigger.BAR_CLOSE,
                    publication_cadence=PublicationCadence.BAR_CLOSE,
                ),
            )

    @classmethod
    def from_qmd_payload(cls, payload: dict[str, Any]) -> "MarketSignal":
        """Validate the shared QMD lifecycle contract at the strategy boundary."""
        required = (
            "signal_id",
            "event_id",
            "signal_key",
            "schema_version",
            "engine_version",
            "ticker",
            "working_timeframe",
            "observed_at",
            "effective_at",
            "state",
            "direction",
            "score",
        )
        if int(payload.get("schema_version") or 0) >= 3:
            required += ("signal_version", "rank_score", "clock")
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            raise ValueError(f"QMD market signal is missing: {', '.join(missing)}")
        state = str(payload["state"]).lower()
        direction = str(payload["direction"]).lower()
        if state not in {"triggered", "updated", "resolved", "expired"}:
            raise ValueError(f"Unsupported QMD signal state: {state}")
        if direction not in {"bullish", "bearish", "neutral"}:
            raise ValueError(f"Unsupported QMD signal direction: {direction}")
        observed_at = _aware_timestamp(payload["observed_at"], "observed_at")
        effective_at = _aware_timestamp(payload["effective_at"], "effective_at")
        if effective_at > observed_at:
            raise ValueError("QMD signal effective_at cannot be later than observed_at")
        expires_at = (
            _aware_timestamp(payload["expires_at"], "expires_at")
            if payload.get("expires_at")
            else None
        )
        domain = SignalDomain(str(payload.get("domain") or SignalDomain.MARKET))
        clock_payload = dict(payload.get("clock") or {})
        clock = ClockContract(
            input_basis=InputBasis(str(clock_payload.get("input_basis") or InputBasis.BAR_DERIVED)),
            calculation_window=str(
                clock_payload.get("calculation_window") or payload["working_timeframe"]
            ),
            evaluation_mode=EvaluationMode(
                str(clock_payload.get("evaluation_mode") or EvaluationMode.CLOSED_ONLY)
            ),
            update_trigger=UpdateTrigger(
                str(clock_payload.get("update_trigger") or UpdateTrigger.BAR_CLOSE)
            ),
            publication_cadence=PublicationCadence(
                str(clock_payload.get("publication_cadence") or PublicationCadence.BAR_CLOSE)
            ),
            publication_interval_ms=(
                int(clock_payload["publication_interval_ms"])
                if clock_payload.get("publication_interval_ms") is not None
                else None
            ),
        )
        return cls(
            signal_id=str(payload["signal_id"]),
            event_id=str(payload["event_id"]),
            signal_key=str(payload["signal_key"]),
            schema_version=int(payload["schema_version"]),
            signal_version=int(payload.get("signal_version") or 1),
            engine_version=str(payload["engine_version"]),
            producer=str(payload.get("producer") or "qmd"),
            ticker=str(payload["ticker"]).upper(),
            working_timeframe=str(payload["working_timeframe"]),
            confirmation_timeframe=(
                str(payload["confirmation_timeframe"])
                if payload.get("confirmation_timeframe")
                else None
            ),
            observed_at=observed_at,
            effective_at=effective_at,
            state=state,  # type: ignore[arg-type]
            direction=direction,  # type: ignore[arg-type]
            score=float(payload.get("score") or 0.0),
            rank_score=float(
                payload.get("rank_score")
                if payload.get("rank_score") is not None
                else abs(float(payload.get("score") or 0.0))
            ),
            confidence=float(payload.get("confidence") or 0.0),
            trigger_reason=str(payload.get("trigger_reason") or ""),
            resolution_reason=str(payload.get("resolution_reason") or ""),
            reference_price=float(payload.get("reference_price") or 0.0),
            invalidation_price=(
                float(payload["invalidation_price"])
                if payload.get("invalidation_price") is not None
                else None
            ),
            expires_at=expires_at,
            evidence=dict(payload.get("evidence") or {}),
            domain=domain,
            clock=clock,
        )


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """A strategy-owned interpretation; this is not an order instruction."""

    signal_id: str
    signal_type: str
    ticker: str
    event_time: datetime
    action: StrategyAction
    direction: SignalDirection
    score: float
    confidence: float
    reason: str
    source_signal_ids: tuple[str, ...] = ()
    working_timeframe: str = ""
    invalidation_price: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrategyIntent:
    """Broker-neutral desired position change emitted by strategy logic."""

    intent_id: str
    ticker: str
    event_time: datetime
    action: StrategyAction
    quantity: float
    reference_price: float
    invalidation_price: float | None = None
    profit_target_price: float | None = None
    trailing_amount: float | None = None
    urgency: Literal[
        "passive_limit",
        "aggressive_limit",
        "market",
        "very_urgent",
        "urgent",
        "regular",
        "patient",
    ] = "aggressive_limit"
    time_in_force: str = "DAY"
    outside_rth: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.intent_id or not self.ticker:
            raise ValueError("Strategy intent identity and ticker are required")
        if self.quantity < 0:
            raise ValueError("Strategy intent quantity cannot be negative")
        if self.action in {
            "enter_long",
            "add_long",
            "reduce_long",
            "take_profit",
            "exit",
            "enter_short",
            "add_short",
            "reduce_short",
            "cover",
        } and self.quantity <= 0:
            raise ValueError(f"{self.action} requires a positive quantity")

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    """Atomic strategy result containing decisions and semantic intents only.

    Broker order requests are deliberately absent. Strategies cannot place,
    modify, or cancel orders; the shared order-management authority exclusively
    translates these semantic intents into broker commands.
    """

    signals: tuple[StrategySignal, ...] = ()
    intents: tuple[StrategyIntent, ...] = ()


def normalize_strategy_evaluation(
    value: StrategyEvaluation | None,
) -> StrategyEvaluation:
    if value is None:
        return StrategyEvaluation()
    if isinstance(value, StrategyEvaluation):
        return value
    raise TypeError("Strategy must return StrategyEvaluation; direct broker orders are forbidden")


def _aware_timestamp(value: Any, field_name: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        raise ValueError(f"QMD signal {field_name} must include a timezone")
    return parsed

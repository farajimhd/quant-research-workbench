from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable


StrategyFactory = Callable[[list[Any]], Any]
ParameterResolver = Callable[[dict[str, Any] | None], dict[str, Any]]
DefinitionFactory = Callable[[], dict[str, Any]]
InputCatalogFactory = Callable[[], list[dict[str, Any]]]
TimeframeResolver = Callable[[dict[str, Any]], set[str]]
ObservationProjector = Callable[[Any, str], dict[str, Any]]
AssignmentEvaluator = Callable[[Any, Any], Any]


@dataclass(frozen=True, slots=True)
class StrategyExecutorRegistration:
    """Installed, code-reviewed execution authority for one immutable Strategy revision."""

    strategy_id: str
    revision: int
    implementation: str
    definition_factory: DefinitionFactory
    parameter_resolver: ParameterResolver
    strategy_factory: StrategyFactory
    input_catalog_factory: InputCatalogFactory
    timeframe_resolver: TimeframeResolver
    observation_projector: ObservationProjector
    assignment_evaluator: AssignmentEvaluator
    executor_schema_version: int = 1

    @property
    def key(self) -> tuple[str, int]:
        return (self.strategy_id, self.revision)

    def definition(self) -> dict[str, Any]:
        definition = dict(self.definition_factory())
        if (
            str(definition.get("strategy_id") or "") != self.strategy_id
            or int(definition.get("revision") or 0) != self.revision
            or str(definition.get("implementation") or "") != self.implementation
        ):
            raise ValueError(
                f"Registered Strategy definition does not match executor {self.strategy_id}@{self.revision}"
            )
        definition["executor"] = {
            "installed": True,
            "schema_version": self.executor_schema_version,
            "key": f"{self.strategy_id}@{self.revision}",
        }
        return definition


_LOCK = RLock()
_REGISTRY: dict[tuple[str, int], StrategyExecutorRegistration] = {}
_BUILTINS_REGISTERED = False


def register_strategy_executor(
    registration: StrategyExecutorRegistration,
    *,
    replace: bool = False,
) -> None:
    if not registration.strategy_id or registration.revision <= 0:
        raise ValueError("Strategy executor identity and positive revision are required")
    if not registration.implementation:
        raise ValueError("Strategy executor implementation identity is required")
    with _LOCK:
        existing = _REGISTRY.get(registration.key)
        if existing is not None and existing != registration and not replace:
            raise ValueError(
                f"Strategy executor {registration.strategy_id}@{registration.revision} is already registered"
            )
        _REGISTRY[registration.key] = registration


def unregister_strategy_executor(strategy_id: str, revision: int) -> None:
    """Remove a registration for isolated tests; application code must not unload executors."""

    with _LOCK:
        _REGISTRY.pop((str(strategy_id), int(revision)), None)


def strategy_executor(
    strategy_id: str,
    revision: int,
) -> StrategyExecutorRegistration:
    _ensure_builtin_executors()
    key = (str(strategy_id or "").strip(), int(revision or 0))
    with _LOCK:
        registration = _REGISTRY.get(key)
    if registration is None:
        raise ValueError(
            f"No installed Strategy executor matches {key[0] or '<missing>'}@{key[1]}; "
            "publish an installed definition revision before starting a runtime"
        )
    return registration


def strategy_executor_optional(
    strategy_id: str,
    revision: int,
) -> StrategyExecutorRegistration | None:
    try:
        return strategy_executor(strategy_id, revision)
    except ValueError:
        return None


def installed_strategy_executors() -> tuple[StrategyExecutorRegistration, ...]:
    _ensure_builtin_executors()
    with _LOCK:
        return tuple(
            _REGISTRY[key]
            for key in sorted(_REGISTRY, key=lambda item: (item[0], item[1]))
        )


def installed_strategy_definitions() -> list[dict[str, Any]]:
    return [registration.definition() for registration in installed_strategy_executors()]


def installed_strategy_input_catalog() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for registration in installed_strategy_executors():
        for source in registration.input_catalog_factory():
            source_id = str(source.get("source_id") or "")
            if source_id:
                rows.setdefault(source_id, dict(source))
    return [rows[source_id] for source_id in sorted(rows)]


def _ensure_builtin_executors() -> None:
    global _BUILTINS_REGISTERED
    with _LOCK:
        if _BUILTINS_REGISTERED:
            return
        from src.trading_runtime.strategy_engine import (
            AssignedLongMomentumStrategy,
            LongMomentumStrategyEngine,
            STRATEGY_ID,
            STRATEGY_REVISION,
            long_momentum_strategy_definition,
            resolve_long_momentum_parameters,
            strategy_input_catalog,
            strategy_observation_source_values,
            strategy_rule_timeframes,
        )

        register_strategy_executor(
            StrategyExecutorRegistration(
                strategy_id=STRATEGY_ID,
                revision=STRATEGY_REVISION,
                implementation=(
                    "src.trading_runtime.strategy_engine.LongMomentumStrategyEngine"
                ),
                definition_factory=long_momentum_strategy_definition,
                parameter_resolver=resolve_long_momentum_parameters,
                strategy_factory=AssignedLongMomentumStrategy,
                input_catalog_factory=strategy_input_catalog,
                timeframe_resolver=strategy_rule_timeframes,
                observation_projector=strategy_observation_source_values,
                assignment_evaluator=LongMomentumStrategyEngine().evaluate,
            )
        )
        _BUILTINS_REGISTERED = True

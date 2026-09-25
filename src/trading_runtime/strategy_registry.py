from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
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
class NumberedStrategyRelease:
    """One complete, sealed trading behavior; executor revision is internal.

    A new entry, exit, sizing, timing, or rule-set behavior needs a new number.
    Dependencies may have separate technical versions, but each release pins
    them exactly. The digest is an integrity seal, not an external signature.
    """

    number: int
    executor_strategy_id: str
    executor_revision: int
    evaluation_interval: str
    input_contracts: tuple[str, ...]
    rule_set_contracts: tuple[str, ...]
    behavior_specification: str
    approved_digest: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "executor_strategy_id": self.executor_strategy_id,
            "executor_revision": self.executor_revision,
            "evaluation_interval": self.evaluation_interval,
            "input_contracts": list(self.input_contracts),
            "rule_set_contracts": list(self.rule_set_contracts),
            "behavior_specification": self.behavior_specification,
        }

    def digest(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return sha256(raw).hexdigest()

    def verify(self) -> None:
        fixed = re.fullmatch(r"([1-9]\d*)ms", self.evaluation_interval)
        interval_valid = (self.evaluation_interval == "events"
                          or fixed is not None and int(fixed.group(1)) % 100 == 0)
        if (type(self.number) is not int or self.number < 1
                or not self.executor_strategy_id
                or self.executor_strategy_id.strip() != self.executor_strategy_id
                or self.executor_revision < 1
                or not interval_valid
                or not self.input_contracts or len(set(self.input_contracts)) != len(self.input_contracts)
                or not self.rule_set_contracts
                or len(set(self.rule_set_contracts)) != len(self.rule_set_contracts)
                or not self.behavior_specification.strip()
                or self.approved_digest != self.digest()):
            raise ValueError("Numbered Strategy release lacks an exact approved seal")


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
_NUMBERED_RELEASES: dict[int, NumberedStrategyRelease] = {}
_BUILTIN_REGISTRY: dict[tuple[str, int], StrategyExecutorRegistration] = {}
_BUILTINS_REGISTERED = False


def register_numbered_strategy(release: NumberedStrategyRelease) -> None:
    """Publish once; there is deliberately no replace or mutable edit API."""
    release.verify()
    _ensure_builtin_executors()
    with _LOCK:
        key = (release.executor_strategy_id.strip(), release.executor_revision)
        if key not in _REGISTRY:
            raise ValueError("Numbered Strategy release lacks an installed executor")
        prior = _NUMBERED_RELEASES.get(release.number)
        if prior is not None and prior != release:
            raise ValueError(f"Strategy {release.number} is immutable; assign a new number")
        _NUMBERED_RELEASES[release.number] = release


def numbered_strategy(number: int) -> NumberedStrategyRelease:
    with _LOCK:
        release = _NUMBERED_RELEASES.get(number)
    if release is None:
        raise ValueError(f"Strategy {number} is not published")
    release.verify()
    return release


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
        if existing is not registration and any(
            (release.executor_strategy_id.strip(), release.executor_revision)
            == registration.key for release in _NUMBERED_RELEASES.values()
        ):
            raise ValueError(
                f"Published Strategy executor {registration.strategy_id}@{registration.revision} "
                "cannot be replaced"
            )
        if existing is not None and existing != registration and not replace:
            raise ValueError(
                f"Strategy executor {registration.strategy_id}@{registration.revision} is already registered"
            )
        _REGISTRY[registration.key] = registration


def unregister_strategy_executor(strategy_id: str, revision: int) -> None:
    """Remove a registration for isolated tests; application code must not unload executors."""

    with _LOCK:
        key = (str(strategy_id), int(revision))
        if any((release.executor_strategy_id.strip(), release.executor_revision) == key
               for release in _NUMBERED_RELEASES.values()):
            raise ValueError("Published Strategy executor cannot be unregistered")
        _REGISTRY.pop(key, None)


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


def typed_persistence_executor(
    strategy_id: str, revision: int,
) -> StrategyExecutorRegistration:
    """Admit only the original built-in executor object to inactive typed mode.

    Legacy registration and replacement remain available; typed persistence
    must fail closed until an explicit catalog exists for another executor.
    """
    registration = strategy_executor(strategy_id, revision)
    with _LOCK:
        if _BUILTIN_REGISTRY.get(registration.key) is not registration:
            raise ValueError(
                f"No typed persistence catalog for {registration.strategy_id}@{registration.revision}"
            )
    return registration


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
            HISTORICAL_STRATEGY_REVISIONS,
            LongMomentumStrategyEngine,
            STRATEGY_ID,
            STRATEGY_REVISION,
            long_momentum_strategy_definition,
            resolve_long_momentum_parameters,
            strategy_input_catalog,
            strategy_observation_source_values,
            strategy_rule_timeframes,
        )

        for revision in (*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION):
            builtin = StrategyExecutorRegistration(
                    strategy_id=STRATEGY_ID,
                    revision=revision,
                    implementation=(
                        "src.trading_runtime.strategy_engine.LongMomentumStrategyEngine"
                    ),
                    definition_factory=lambda revision=revision: long_momentum_strategy_definition(
                        revision=revision
                    ),
                    parameter_resolver=lambda parameters, revision=revision: resolve_long_momentum_parameters(
                        parameters,
                        revision=revision,
                    ),
                    strategy_factory=lambda assignments, revision=revision: AssignedLongMomentumStrategy(
                        assignments,
                        revision=revision,
                    ),
                    input_catalog_factory=strategy_input_catalog,
                    timeframe_resolver=strategy_rule_timeframes,
                    observation_projector=strategy_observation_source_values,
                    assignment_evaluator=LongMomentumStrategyEngine(
                        revision=revision
                    ).evaluate,
                )
            register_strategy_executor(builtin)
            _BUILTIN_REGISTRY[builtin.key] = builtin
        _BUILTINS_REGISTERED = True

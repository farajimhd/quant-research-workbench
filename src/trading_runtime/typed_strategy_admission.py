"""Off-by-default admission preflight for future typed assignment persistence.

This deliberately does not change legacy strategy parameter resolution or
activate typed storage. A complete typed state catalog is still required.
"""
from __future__ import annotations

from typing import Any, Mapping

from src.trading_runtime.strategy_registry import typed_persistence_executor


def _closed_override(value: Any, template: Any, path: str) -> None:
    if isinstance(template, dict):
        if not isinstance(value, Mapping):
            raise ValueError(f"typed parameter {path} must remain a mapping")
        unknown = set(value) - set(template)
        if unknown:
            raise ValueError(f"unknown typed parameter {path}.{sorted(unknown)[0]}")
        for key, child in value.items():
            _closed_override(child, template[key], f"{path}.{key}")
    elif isinstance(template, list):
        if not isinstance(value, list):
            raise ValueError(f"typed parameter {path} must remain a list")
        if value and not template:
            raise ValueError(f"typed parameter {path} has no item catalog")
        # A list whose default has several item variants needs an explicit
        # per-family catalog; one exemplar cannot safely admit new variants.
        if value and len(template) != 1:
            raise ValueError(f"typed parameter {path} needs a versioned list catalog")
        for index, child in enumerate(value):
            _closed_override(child, template[0], f"{path}[{index}]")


def resolve_typed_persistence_parameters(
    strategy_id: str, revision: int, overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate closed built-in keys before delegating to the legacy resolver.

    The returned parameters are not publishable until the full normalized
    parameter and mutable-state families have been defined and hydrated.
    """
    registration = typed_persistence_executor(strategy_id, revision)
    if overrides is not None and not isinstance(overrides, Mapping):
        raise ValueError("typed parameter overrides must be a mapping")
    from src.trading_runtime.strategy_engine import default_long_momentum_parameters

    template = default_long_momentum_parameters(revision=revision)
    _closed_override(dict(overrides or {}), template, "parameters")
    return registration.parameter_resolver(dict(overrides or {}))

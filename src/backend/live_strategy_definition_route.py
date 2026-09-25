"""Read-only staged route port; never an executable Strategy authority."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from src.backend.live_strategy_definition_bootstrap import (
    DefinitionBootstrapSettings, bootstrap_staged_definitions,
)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class StagedDefinitionReadRoute:
    """Each request repeats storage preflight and complete Keeper-attested read."""

    def __init__(self, settings: DefinitionBootstrapSettings, *,
                 keeper_client: Any, client_factory: Callable[..., Any] | None = None) -> None:
        if not isinstance(settings, DefinitionBootstrapSettings):
            raise TypeError("staged definition route needs explicit settings")
        self._settings = settings
        self._keeper_client = keeper_client
        self._client_factory = client_factory

    def _rows(self) -> list[dict[str, Any]]:
        catalog = bootstrap_staged_definitions(
            self._settings, keeper_client=self._keeper_client,
            client_factory=self._client_factory)
        return [_thaw(value) for value in catalog.definitions.values()]

    def list_definitions(self, *, latest_only: bool) -> list[dict[str, Any]]:
        rows = self._rows()
        if latest_only:
            newest = {}
            for row in rows:
                key = row["strategy_id"]
                if key not in newest or row["revision"] > newest[key]["revision"]:
                    newest[key] = row
            rows = list(newest.values())
        return sorted(rows, key=lambda row: (
            row["name"], row["strategy_id"], -row["revision"]))

    def get_definition(self, strategy_id: str,
                       revision: int | None = None) -> dict[str, Any]:
        rows = [row for row in self._rows()
                if row["strategy_id"] == strategy_id
                and (revision is None or row["revision"] == revision)]
        if not rows:
            raise KeyError(strategy_id)
        return max(rows, key=lambda row: row["revision"])

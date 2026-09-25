"""Explicit, inactive typed-definition cold bootstrap; never used by SQLite routes.

This verifies the entire installed executor catalog with injected transports.
It does not authorize live execution: no exclusive configuration admission
fence yet prevents the Keeper head changing after bootstrap returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import platform
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from src.backend.live_strategy_definition_clickhouse import ClickHouseDefinitionStorage
from src.backend.live_strategy_definition_cold_reader import (
    DefinitionHead, KeeperDefinitionHeadReader, cold_read_installed_definition,
)
from src.backend.live_strategy_definition_preflight import staged_definition_storage_preflight
from src.trading_runtime.strategy_registry import (
    installed_strategy_executors, typed_persistence_executor,
)


@dataclass(frozen=True)
class DefinitionBootstrapSettings:
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str = field(repr=False)
    keeper_endpoint: str = "127.0.0.1:9181"
    mode: str = "staged_read_only"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.clickhouse_url)
        managed_workstation_http = (
            self.clickhouse_url == "http://DESKTOP-SAAI85T:18123"
            and platform.node().upper() == "DESKTOP-SAAI85T"
        )
        if (self.mode != "staged_read_only"
                or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and parsed.hostname not in {
                    "127.0.0.1", "localhost"} and not managed_workstation_http)
                or self.clickhouse_user != "trading_journal_writer"
                or not isinstance(self.clickhouse_password, str)
                or len(self.clickhouse_password) < 40
                or self.keeper_endpoint != "127.0.0.1:9181"):
            raise ValueError("typed definition bootstrap settings are incomplete or unsafe")


@dataclass(frozen=True)
class StagedDefinitionCatalog:
    definitions: Mapping[tuple[str, int], Mapping[str, Any]]
    heads: Mapping[tuple[str, int], DefinitionHead]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("typed definition cold value is unmodeled")


def bootstrap_staged_definitions(
    settings: DefinitionBootstrapSettings, *, keeper_client: Any,
    client_factory: Callable[..., Any] | None = None,
) -> StagedDefinitionCatalog:
    """Control-plane-only: exact CH preflight and full Keeper-attested cold read.

    The created ClickHouse client is closed before returning. Keeper remains
    caller-owned. No SQLite or filesystem path is inspected.
    """
    if not isinstance(settings, DefinitionBootstrapSettings):
        raise TypeError("explicit typed definition bootstrap settings are required")
    if (not getattr(keeper_client, "connected", False)
            or getattr(keeper_client, "client_id", None) is None):
        raise RuntimeError("typed definition Keeper session is unavailable")
    if client_factory is None:
        from research.mlops.clickhouse import ClickHouseHttpClient
        client_factory = ClickHouseHttpClient
    client = client_factory(
        settings.clickhouse_url, settings.clickhouse_user,
        settings.clickhouse_password, timeout_seconds=5.0)
    try:
        staged_definition_storage_preflight(client)
        storage = ClickHouseDefinitionStorage(client)
        keeper = KeeperDefinitionHeadReader(
            keeper_client, endpoint=settings.keeper_endpoint)
        registrations = installed_strategy_executors()
        if not registrations:
            raise RuntimeError("typed definition executor catalog is empty")
        definitions: dict[tuple[str, int], Mapping[str, Any]] = {}
        heads: dict[tuple[str, int], DefinitionHead] = {}
        for registration in registrations:
            key = registration.key
            typed_persistence_executor(*key)
            first = keeper.read_head(*key)
            value = cold_read_installed_definition(
                storage, keeper, strategy_id=key[0], strategy_revision=key[1])
            if keeper.read_head(*key) != first:
                raise RuntimeError("typed definition head changed during bootstrap")
            definitions[key] = _freeze(value)
            heads[key] = first
        # A head changed while another revision was loading must not produce a
        # mixed catalog. This is still a point-in-time check, not an execution lease.
        if any(keeper.read_head(*key) != head for key, head in heads.items()):
            raise RuntimeError("typed definition catalog changed during bootstrap")
        return StagedDefinitionCatalog(MappingProxyType(definitions),
                                       MappingProxyType(heads))
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

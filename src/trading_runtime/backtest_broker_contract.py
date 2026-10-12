"""Normalized, immutable broker settings for the shared future Backtest path.

No profile defaults are inferred here. The app supplies its complete selected
SimulationConfig and certified liquidity-plan identity before a run can start.
The normal journal owns publication/recovery; this projector owns no storage.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from hashlib import sha256
import json
import math
import re
from dataclasses import dataclass, field
from collections.abc import Mapping
from types import MappingProxyType

from .columnar_strategy_schema import BROKER
from .simulated_broker import SimulationConfig


def _money(value):
    if isinstance(value, bool):
        raise ValueError("Broker money must be an explicit finite decimal")
    result = Decimal(str(value))
    if (not result.is_finite() or result < 0 or result >= Decimal("1e28")
            or result.as_tuple().exponent < -10):
        raise ValueError("Broker money exceeds Decimal(38,10)")
    with localcontext() as context:
        context.prec = 80
        return result.quantize(Decimal("0.0000000001"))


def _hash(row):
    return sha256(json.dumps(row, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=str).encode()).hexdigest()


def project_broker_contract(run_id: str, config: SimulationConfig, *, liquidity_plan_hash: str):
    if (type(config) is not SimulationConfig or not isinstance(run_id, str)
            or not 1 <= len(run_id) <= 128 or any(ord(c) < 32 for c in run_id)
            or re.fullmatch(r"[0-9a-f]{64}", liquidity_plan_hash or "") is None):
        raise ValueError("Broker contract requires a bounded run and pinned liquidity plan")
    if config.base_currency != "USD" or config.allow_short:
        raise ValueError("Current broker contract covers USD long-only runs")
    delay = Decimal(str(config.new_order_activation_delay_ms)) * 1000
    if delay != delay.to_integral_value() or not 0 <= delay < 2 ** 32:
        raise ValueError("Broker delay must be exactly representable in microseconds")
    marketable = (config.liquidity_participation if config.marketable_liquidity_participation is None
                  else config.marketable_liquidity_participation)
    for value in (config.liquidity_participation, marketable):
        if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError("Broker participation must be finite and in (0,1]")
    row = dict(run_id=run_id, liquidity_resolution_ms=config.liquidity_resolution_ms,
        liquidity_product=config.liquidity_source, liquidity_plan_hash=liquidity_plan_hash,
        commission_per_share=_money(config.commission_per_share),
        minimum_commission=_money(config.minimum_commission),
        market_slippage_bps=_money(config.market_slippage_bps), activation_delay_us=int(delay),
        liquidity_participation=float(config.liquidity_participation),
        marketable_liquidity_participation=float(marketable))
    row["content_hash"] = _hash(row)
    return row


def restore_broker_contract(row, *, run_id, content_hash, liquidity_plan_hash, initial_cash):
    """Restore only the pinned normalized run contract, never current defaults."""
    if set(row) != {name for name, _ in BROKER.columns}:
        raise ValueError("Broker contract schema differs")
    normalized = dict(row)
    for name in ("commission_per_share", "minimum_commission", "market_slippage_bps"):
        normalized[name] = _money(normalized[name])
    for name in ("liquidity_resolution_ms", "activation_delay_us"):
        value = normalized[name]
        if isinstance(value, bool) or (not isinstance(value, (int, str))) or str(int(value)) != str(value):
            raise ValueError("Broker integer field is invalid")
        normalized[name] = int(value)
    for name in ("liquidity_participation", "marketable_liquidity_participation"):
        if isinstance(normalized[name], bool):
            raise ValueError("Broker participation is invalid")
        normalized[name] = float(normalized[name])
    if (normalized["run_id"] != run_id or normalized["liquidity_plan_hash"] != liquidity_plan_hash
            or normalized["content_hash"] != content_hash
            or _hash({k: v for k, v in normalized.items() if k != "content_hash"}) != content_hash):
        raise ValueError("Broker contract differs from pinned run identity")
    config = SimulationConfig(initial_cash=initial_cash,
        commission_per_share=float(normalized["commission_per_share"]),
        minimum_commission=float(normalized["minimum_commission"]),
        market_slippage_bps=float(normalized["market_slippage_bps"]),
        new_order_activation_delay_ms=normalized["activation_delay_us"] / 1000,
        liquidity_resolution_ms=normalized["liquidity_resolution_ms"],
        liquidity_participation=normalized["liquidity_participation"],
        marketable_liquidity_participation=normalized["marketable_liquidity_participation"])
    if project_broker_contract(run_id, config, liquidity_plan_hash=liquidity_plan_hash) != normalized:
        raise ValueError("Broker settings cannot round-trip exactly to the existing broker")
    return config


@dataclass(frozen=True, slots=True)
class BacktestBrokerContract:
    """Immutable validated settings; starting cash belongs to the run definition.

    This content does not prove publication or certify liquidity coverage.
    """
    row: Mapping
    initial_cash: float
    config: SimulationConfig = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.row, Mapping):
            raise ValueError("Broker contract must be a named scalar row")
        row = dict(self.row)
        config = restore_broker_contract(row, run_id=row.get("run_id"),
            content_hash=row.get("content_hash"), liquidity_plan_hash=row.get("liquidity_plan_hash"),
            initial_cash=self.initial_cash)
        canonical = project_broker_contract(row["run_id"], config,
            liquidity_plan_hash=row["liquidity_plan_hash"])
        object.__setattr__(self, "row", MappingProxyType(canonical))
        object.__setattr__(self, "config", config)

    def require_config(self, config, *, run_id, content_hash, liquidity_plan_hash):
        if (type(config) is not SimulationConfig or config.initial_cash != self.initial_cash
                or self.row["content_hash"] != content_hash
                or self.row != project_broker_contract(run_id, config, liquidity_plan_hash=liquidity_plan_hash)):
            raise ValueError("Broker contract differs from selected native run settings")
        return self.config

    def wire_row(self):
        """Return named transport scalars; no JSON/blob column is introduced."""
        return {name: format(value, "f") if isinstance(value, Decimal) else value
            for name, value in self.row.items()}


def read_broker_contract(client, *, run_id, content_hash, liquidity_plan_hash, initial_cash):
    """Bounded SELECT-only readback on a caller-owned cold connection."""
    from .arte_journal_schema import storage_preflight
    from .arte_journal_writer import _literal
    if (type(run_id) is not str or not 1 <= len(run_id) <= 128
            or any(ord(c) < 32 for c in run_id)
            or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (content_hash, liquidity_plan_hash))):
        raise ValueError("Broker readback needs pinned run and plan identities")
    if client.execute("SELECT getSetting('readonly')").strip() != "1":
        raise RuntimeError("Broker contract requires a server-enforced cold reader")
    storage_preflight(client, tables=(BROKER,))
    columns = ",".join(name for name, _ in BROKER.columns)
    response = client.execute(f"SELECT {columns} FROM arte.{BROKER.name} "
        f"WHERE run_id={_literal(run_id)} ORDER BY run_id LIMIT 2 FORMAT JSONEachRow")
    if type(response) is not str or len(response.encode("utf-8")) > 16_384:
        raise ValueError("Broker contract readback exceeds its bounded response")
    lines = [line for line in response.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Broker contract is absent or duplicated")
    def unique_fields(pairs):
        row = {}
        for name, value in pairs:
            if name in row:
                raise ValueError("Broker contract has duplicate named fields")
            row[name] = value
        return row
    row = json.loads(lines[0], object_pairs_hook=unique_fields)
    if not isinstance(row, Mapping):
        raise ValueError("Broker contract must be a named scalar row")
    config = restore_broker_contract(row, run_id=run_id, content_hash=content_hash,
        liquidity_plan_hash=liquidity_plan_hash, initial_cash=initial_cash)
    contract = BacktestBrokerContract(row, initial_cash)
    contract.require_config(config, run_id=run_id, content_hash=content_hash,
        liquidity_plan_hash=liquidity_plan_hash)
    return contract

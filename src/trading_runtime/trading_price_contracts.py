"""Staged shared exact-price contracts; importing this module installs nothing.

Legacy definitions remain immutable. Callers must explicitly select the new
families; writer admission and recovery integration are separate requirements.
"""
from dataclasses import replace
from types import MappingProxyType

from .trading_price_codec import decode_trading_price, encode_trading_price


PRICE_FAMILIES = MappingProxyType({
    "trading_strategy_signal_v1": ("trading_strategy_signal_exact_price_v1", ("invalidation_price", "decision_reference_price")),
    "trading_strategy_signal_v2": ("trading_strategy_signal_exact_price_v2", ("invalidation_price", "decision_reference_price")),
    "trading_execution_v1": ("trading_execution_exact_price_v1", ("price", "average_price", "signal_price", "arrival_midpoint")),
    "trading_order_command_v1": ("trading_order_command_exact_price_v1", ("limit_price", "aux_price")),
    "trading_order_transition_v1": ("trading_order_transition_exact_price_v1", ("average_fill_price",)),
    "trading_oms_group_state_v1": ("trading_oms_group_state_exact_price_v1", ("deferred_reprice_from", "deferred_reprice_to", "high_water_price", "low_water_price", "current_limit_price")),
    "trading_oms_order_state_v1": ("trading_oms_order_state_exact_price_v1", ("limit_price", "aux_price")),
    "trading_oms_execution_step_v1": ("trading_oms_execution_step_exact_price_v1", ("price",)),
    "trading_order_reprice_v4": ("trading_order_reprice_exact_price_v4", ("requested_price",)),
    "trading_order_modify_command_v1": ("trading_order_modify_command_exact_price_v1", ("limit_price", "aux_price")),
    "trading_protection_reconciliation_action_v4": ("trading_protection_reconciliation_action_exact_price_v4", ("stop_price", "target_price", "from_stop", "to_stop")),
})
EXACT_PRICE_FAMILY_NAMES = frozenset(target for target, _ in PRICE_FAMILIES.values())


def exact_price_storage_contracts():
    """Return the registered shared schemas without installing any tables."""
    from .arte_journal_writer import _EXACT_PRICE_CONTRACTS
    return tuple(_EXACT_PRICE_CONTRACTS.values())


def program_exact_price_storage_contracts():
    """Current Backtest V4 uses the v2 signal family; retain legacy catalogues."""
    return tuple(table for table in exact_price_storage_contracts()
                 if table.name != "trading_strategy_signal_exact_price_v1")


def exact_price_contracts(base_contracts):
    """Derive closed, named scalar schemas and retain every nonprice field."""
    result = {}
    for source, (target, fields) in PRICE_FAMILIES.items():
        base = base_contracts[source]
        kinds = dict(base.columns)
        for field in fields:
            if kinds.get(field, "").replace(" ", "") not in ("Decimal(38,10)", "Nullable(Decimal(38,10))"):
                raise ValueError(f"Unexpected legacy price contract: {source}.{field}")
            if field in {part.strip() for part in base.order.split(",")}:
                raise ValueError("Price sorting keys require an explicit new ordering contract")
        columns = tuple(
            (field + "_bits", "Nullable(UInt64)" if kind.startswith("Nullable(") else "UInt64")
            if field in fields else (field, kind)
            for field, kind in base.columns
        )
        result[target] = replace(base, name=target, columns=columns)
    return MappingProxyType(result)


def transform_price_fields(source, values, base_contracts, *, restore=False):
    """Transform unhashed rows only; hashes must cover the selected schema.

Zero remains valid for existing OMS unset-price sentinels. Negative and
nonfinite values remain invalid. Nonprice columns are copied without coercion.
"""
    if type(restore) is not bool:
        raise ValueError("Restore mode must be an explicit boolean")
    _, fields = PRICE_FAMILIES[source]
    if "content_hash" in values:
        raise ValueError("Price representation conversion requires an unhashed row")
    result = dict(values)
    kinds = dict(base_contracts[source].columns)
    for field in fields:
        encoded = field + "_bits"
        incoming, outgoing = (encoded, field) if restore else (field, encoded)
        if outgoing in result or incoming not in result:
            raise ValueError(f"Missing or conflicting price field: {incoming}")
        codec = decode_trading_price if restore else encode_trading_price
        result[outgoing] = codec(result.pop(incoming), allow_zero=True,
                                nullable=kinds[field].startswith("Nullable("))
    return result

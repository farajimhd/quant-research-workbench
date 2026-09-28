"""Isolated normalized activation families for immutable Strategy 1 live runs.

Legacy activation writers must never receive INSERT grants on these tables.
The four relations retain the existing typed scalar contract while a separate
principal and Keeper dispatch fence own publication and cold recovery. This
module declares schema only; runtime code must not create tables.
"""
from __future__ import annotations

from .arte_journal_schema import ACTIVATION_TABLES, TableContract


LEGACY_TO_STRATEGY_ONE = {
    table.name: table.name.replace(
        "trading_activation", "trading_strategy_one_activation", 1)
    for table in ACTIVATION_TABLES
}

STRATEGY_ONE_ACTIVATION_TABLES = tuple(
    TableContract(
        LEGACY_TO_STRATEGY_ONE[table.name], table.columns,
        table.partition, table.order, table.allow_nullable_key,
    )
    for table in ACTIVATION_TABLES
)


def strategy_one_activation_table(logical_name: str) -> str:
    """Resolve a legacy logical family to its distinct Strategy 1 authority."""
    try:
        return LEGACY_TO_STRATEGY_ONE[logical_name]
    except KeyError as exc:
        raise ValueError("Unknown normalized activation family") from exc

"""Compatibility imports for prepared ladder callers; execution is rule-selected."""
from .independent_lot_protection import (
    reconcile_independent_lot_protection as reconcile_ladder_protection,
    recover_ladder_repair_outcome,
)

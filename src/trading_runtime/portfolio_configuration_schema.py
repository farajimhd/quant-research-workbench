"""Shared typed portfolio-policy configuration for future strategy releases.

Operator-staged contracts only. No runtime DDL or strategy-owned tables.
Collection members are normalized children of the immutable policy revision.
The existing PortfolioPolicy owns field semantics and defaults; these contracts
do not select new limits or alter legacy configuration persistence.
"""
from .arte_journal_schema import TableContract


POLICY = TableContract(
    "trading_portfolio_policy_configuration_v1",
    (("configuration_id", "UUID"), ("policy_id", "String"), ("revision", "UInt32"),
     ("eligible_equity_fraction", "Float64"),
     ("minimum_cash_reserve", "Float64"), ("entry_fee_buffer_bps", "Float64"),
     ("maximum_buying_power_utilization", "Float64"),
     ("maximum_gross_exposure", "Float64"), ("maximum_net_long_exposure", "Float64"),
     ("maximum_net_short_exposure", "Float64"),
     ("maximum_position_fraction", "Float64"), ("maximum_ticker_fraction", "Float64"),
     ("maximum_strategy_fraction", "Float64"), ("maximum_sector_fraction", "Float64"),
     ("maximum_industry_fraction", "Float64"),
     ("maximum_correlated_group_fraction", "Float64"),
     ("maximum_planned_risk_fraction", "Float64"), ("maximum_open_risk_fraction", "Float64"),
     ("maximum_open_positions", "UInt32"), ("maximum_order_quantity", "Float64"),
     ("maximum_order_notional", "Float64"), ("maximum_daily_loss", "Float64"),
     ("maximum_drawdown", "Float64"), ("daily_loss_warning", "Float64"),
     ("emergency_loss", "Float64"), ("maximum_snapshot_age_ms", "UInt32"),
     ("maximum_protection_slices", "UInt32"), ("maximum_internal_reaction_ms", "UInt32"),
     ("allow_long", "UInt8"), ("allow_short", "UInt8"), ("allow_margin", "UInt8"),
     ("allow_unsettled_cash", "UInt8"), ("allow_outside_rth", "UInt8"),
     ("allow_overnight", "UInt8"), ("block_on_unattributed_position", "UInt8"),
     ("allow_stop_limit_protection", "UInt8"), ("allow_partial_profit_pocket", "UInt8"),
     ("allow_emergency_auto_liquidation", "UInt8"),
     ("membership_count", "UInt32"), ("membership_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "tuple()", "configuration_id,policy_id,revision")

MEMBERSHIP = TableContract(
    "trading_portfolio_policy_membership_v1",
    (("configuration_id", "UUID"), ("policy_id", "String"), ("revision", "UInt32"),
     ("collection", "Enum8('allowed_security_types'=1,'allowed_currencies'=2,"
      "'restricted_symbols'=3,'allowed_execution_policies'=4,'allowed_protection_profiles'=5)"),
     ("ordinal", "UInt32"), ("member", "String"), ("content_hash", "FixedString(64)")),
    "tuple()", "configuration_id,policy_id,revision,collection,ordinal")

COLLECTION_FIELDS = (
    "allowed_security_types", "allowed_currencies", "restricted_symbols",
    "allowed_execution_policies", "allowed_protection_profiles",
)
TABLES = (POLICY, MEMBERSHIP)


def ddl():
    """Return reviewable centrally owned DDL without executing it."""
    return tuple(table.ddl() for table in TABLES)

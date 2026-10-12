"""Opt-in storage and grant audit for the shared program decision writer."""
from .rule_decision_basis_schema import STAGED_TABLES


def rule_decision_write_tables():
    from .arte_journal_writer import v4_journal_write_tables
    return v4_journal_write_tables() | frozenset(table.name for table in STAGED_TABLES)


def preflight_rule_decision_writer(client):
    """Audit exact storage placement and least-privilege grants without writes."""
    from .arte_journal_writer import fixed_backtest_v2_contracts, journal_permission_preflight
    from .rule_decision_publication import preflight_rule_decision_storage
    if (getattr(client, "live_v4_lease", None) is not None
            or getattr(client, "automatic_ladder_profile", False)
            or getattr(client, "entry_spread_risk_profile", False)):
        raise ValueError("Program writer requires its independent Backtest profile")
    preflight_rule_decision_storage(client)
    writable = rule_decision_write_tables()
    readonly = frozenset(table.name for table in fixed_backtest_v2_contracts()) - writable
    journal_permission_preflight(client, journal_tables=writable, read_only_tables=readonly)

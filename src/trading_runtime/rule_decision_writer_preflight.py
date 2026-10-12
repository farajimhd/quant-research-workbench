"""Opt-in storage and grant audit for the shared program decision writer."""
from .rule_decision_basis_schema import STAGED_TABLES
from dataclasses import dataclass

_AUDIT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _ProgramWriterAudit:
    client: object
    dispatch: object
    lease: object
    user: object
    base_url: object
    contracts: tuple
    token: object


def _require_profile(client):
    if (getattr(client, "live_v4_lease", None) is not None
            or getattr(client, "automatic_ladder_profile", False)
            or getattr(client, "entry_spread_risk_profile", False)):
        raise ValueError("Program writer requires its independent Backtest profile")
    if getattr(client, "program_decision_profile", False) is not True:
        raise RuntimeError("Program writer requires its dedicated principal")


def rule_decision_write_tables():
    from .arte_journal_writer import v4_journal_write_tables
    from .trading_price_contracts import exact_price_storage_contracts
    from .arte_entry_acquisition_freeze import TABLES as ENTRY_FREEZE_TABLES
    return v4_journal_write_tables() | frozenset(table.name for table in
        (*STAGED_TABLES, *exact_price_storage_contracts(), *ENTRY_FREEZE_TABLES))


def preflight_rule_decision_writer(client):
    """Audit exact storage placement and least-privilege grants without writes."""
    from .arte_journal_writer import fixed_backtest_v2_contracts, journal_permission_preflight
    from .rule_decision_publication import preflight_rule_decision_storage
    from .rule_decision_publication import rule_decision_storage_contracts
    _require_profile(client)
    client._program_writer_audit = None
    if client.execute("SELECT currentUser()").strip() != "backtest_v4_program_runner":
        raise RuntimeError("Program writer requires its dedicated principal")
    preflight_rule_decision_storage(client)
    writable = rule_decision_write_tables()
    readonly = frozenset(table.name for table in fixed_backtest_v2_contracts()) - writable
    journal_permission_preflight(client, journal_tables=writable, read_only_tables=readonly)
    audit = _ProgramWriterAudit(client, getattr(client, "typed_insert_dispatch", None),
        getattr(client, "backtest_v4_lease", None), getattr(client, "user", None),
        getattr(client, "base_url", None), rule_decision_storage_contracts(), _AUDIT_TOKEN)
    client._program_writer_audit = audit
    return audit


def require_rule_decision_writer_audit(client):
    """Reuse only a same-client startup audit; changed ownership fails closed."""
    from .rule_decision_publication import rule_decision_storage_contracts
    audit = getattr(client, "_program_writer_audit", None)
    if audit is None:
        return preflight_rule_decision_writer(client)
    _require_profile(client)
    if (type(audit) is not _ProgramWriterAudit or audit.token is not _AUDIT_TOKEN
            or audit.client is not client
            or audit.dispatch is not getattr(client, "typed_insert_dispatch", None)
            or audit.lease is not getattr(client, "backtest_v4_lease", None)
            or audit.user != getattr(client, "user", None)
            or audit.base_url != getattr(client, "base_url", None)
            or audit.contracts != rule_decision_storage_contracts()):
        raise RuntimeError("Program writer startup audit scope changed")
    return audit

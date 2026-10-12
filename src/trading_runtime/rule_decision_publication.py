"""Prepare staged decision families under the shared V4 commit seal.

No INSERT, registration or Keeper operation occurs here. The registered writer
must independently prove source and financial authority before publication.
"""
from dataclasses import dataclass
from types import MappingProxyType

from .arte_journal_commit_v4 import prepare_commit_v4, verify_commit_v4
from .arte_journal_writer import _sealed_families, _v4_family_table
from .rule_decision_basis_projection import RuleDecisionBasisBatch, _seal
from .rule_decision_basis_schema import BASIS, INTENT, STAGED_TABLES


def rule_decision_storage_contracts():
    """Exact opt-in catalog; do not alter existing V4 startup or legacy tables."""
    from .arte_journal_writer import v4_storage_contracts
    from .trading_price_contracts import exact_price_storage_contracts
    from .arte_entry_acquisition_freeze import TABLES as ENTRY_FREEZE_TABLES
    from .backtest_journal_price_contract import PRICE_CONTRACT
    from .columnar_strategy_schema import BROKER
    contracts = (*v4_storage_contracts(), *STAGED_TABLES, *exact_price_storage_contracts(), *ENTRY_FREEZE_TABLES, PRICE_CONTRACT, BROKER)
    by_name = {}
    for contract in contracts:
        prior = by_name.setdefault(contract.name, contract)
        if prior != contract:
            raise ValueError("Rule decision storage has conflicting shared contracts")
    return tuple(by_name.values())


def preflight_rule_decision_storage(client):
    """Read-only schema/policy/actual-part audit; missing tables fail closed."""
    from .arte_journal_writer import storage_preflight
    storage_preflight(client, tables=rule_decision_storage_contracts())


@dataclass(frozen=True, slots=True)
class PreparedRuleDecisionCommit:
    commit: object
    family_seals: tuple
    families: tuple


@dataclass(frozen=True, slots=True)
class QualifiedRuleDecisionBatch:
    unit: RuleDecisionBasisBatch
    qualification: object

    def __post_init__(self):
        _check_rule_decision_qualification(self.unit, self.qualification)

    @property
    def base(self):
        return self.unit.base


@dataclass(frozen=True, slots=True)
class RuleDecisionInsertRequest:
    table: str
    run_id: str
    batch_id: str
    last_sequence: int
    token: str
    query_id: str
    sql: bytes


def prepare_rule_decision_insert_requests(prepared):
    """Encode staged supplements for the existing registered binary transport.

    Returns requests only. No client is opened and no INSERT is dispatched.
    """
    from .arte_journal_rowbinary import encode_journal_rows
    from .arte_typed_insert_dispatch import typed_insert_query_id
    from .arte_journal_writer import _literal
    if type(prepared) is not PreparedRuleDecisionCommit:
        raise ValueError("Rule decision INSERT preparation requires its commit")
    result = []
    for name, rows in prepared.families:
        if name not in {BASIS.name, INTENT.name}:
            continue
        table = BASIS if name == BASIS.name else INTENT
        normalized = []
        for row in rows:
            sealed = _seal(table, {key: value for key, value in row.items() if key != "content_hash"})
            if sealed != row:
                raise ValueError("Rule decision INSERT scalar integrity differs")
            normalized.append(sealed)
        token = f"{prepared.commit['batch_id']}:{name}"
        header = (f"INSERT INTO arte.{name} ({','.join(column for column, _ in table.columns)}) "
            "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"precise_float_parsing=1,insert_deduplication_token={_literal(token)} FORMAT RowBinary\n")
        result.append(RuleDecisionInsertRequest(name, prepared.commit["run_id"], prepared.commit["batch_id"],
            prepared.commit["last_sequence"], token,
            typed_insert_query_id(prepared.commit["run_id"], name, token),
            header.encode("utf-8") + encode_journal_rows(table.columns, tuple(normalized))))
    return tuple(result)


def prepare_rule_decision_commit(unit, *, committed_at):
    """Seal the original typed completion and scalar decision children together."""
    if type(unit) is not RuleDecisionBasisBatch or unit.base.status != "running":
        raise ValueError("Rule decision preparation requires its running typed transport")
    # Revalidate the immutable supplement before sealing its standard base.
    unit = RuleDecisionBasisBatch(unit.base, unit.basis, unit.intents)
    base = unit.base
    families = tuple((name, rows) for name, rows in (
        *((_v4_family_table(name), rows) for name, rows in _sealed_families(base)),
        (BASIS.name, (unit.basis,)), (INTENT.name, unit.intents)) if rows)
    commit, seals = prepare_commit_v4(run_id=base.run_id, run_month=base.run_month,
        attempt_id=base.attempt_id, batch_id=base.batch_id, prior_batch_id=base.prior_batch_id,
        first_sequence=base.first_sequence, last_sequence=base.last_sequence,
        source_cursor=base.source_cursor, status=base.status,
        sealed_families=families, committed_at=committed_at)
    verify_commit_v4(commit, seals, detail_identities={name: tuple(
        (row["record_id"], row["content_hash"]) for row in rows) for name, rows in families})
    return PreparedRuleDecisionCommit(MappingProxyType(dict(commit)),
        tuple(MappingProxyType(dict(row)) for row in seals),
        tuple((name, tuple(MappingProxyType(dict(row)) for row in rows)) for name, rows in families))


def _check_rule_decision_qualification(unit, qualified):
    from src.backend.rule_decision_reselection import QualifiedRuleDecision, _NativeDecisionEvidence, _NATIVE_DECISION_PROOF
    from .rule_decision_basis_projection import rule_decision_semantic_hash
    evidence = getattr(qualified, "_proof", None)
    if (type(unit) is not RuleDecisionBasisBatch or type(qualified) is not QualifiedRuleDecision
            or type(evidence) is not _NativeDecisionEvidence or evidence.token is not _NATIVE_DECISION_PROOF
            or (qualified.run_id, qualified.basis_sequence, qualified.semantic_hash)
               != (evidence.run_id, evidence.basis_sequence, evidence.semantic_hash)
            or qualified.run_id != unit.base.run_id
            or qualified.basis_sequence != unit.basis["basis_sequence"]
            or qualified.semantic_hash != rule_decision_semantic_hash(unit.basis, unit.intents)):
        raise ValueError("Rule decision commit lacks matching native qualification")


def prepare_qualified_rule_decision_commit(unit, qualified, *, committed_at):
    """Require native financial/reselection evidence bound to the exact rows."""
    _check_rule_decision_qualification(unit, qualified)
    return prepare_rule_decision_commit(unit, committed_at=committed_at)


def verify_rule_decision_commit_readback(prepared, families):
    """Verify persisted UTC scalars before accepting the shared family seal."""
    from .arte_journal_reader import _verified_row
    if type(prepared) is not PreparedRuleDecisionCommit:
        raise ValueError("Rule decision readback requires its prepared commit")
    families = tuple(families)
    expected = {name: len(rows) for name, rows in prepared.families}
    if len(families) != len(expected) or {name for name, _ in families} != set(expected):
        raise ValueError("Rule decision readback family coverage differs")
    identities = {}
    for name, rows in families:
        rows = tuple(rows)
        if len(rows) != expected[name]:
            raise ValueError("Rule decision readback row count differs")
        verified = []
        for row in rows:
            if name in {BASIS.name, INTENT.name}:
                table = BASIS if name == BASIS.name else INTENT
                canonical = _seal(table, {key: value for key, value in row.items() if key != "content_hash"})
                if canonical["content_hash"] != row.get("content_hash"):
                    raise ValueError("Rule decision readback scalar hash differs")
            else:
                canonical = _verified_row("trading_strategy_signal_v1" if name == "trading_strategy_signal_v2" else name, row)
            if (canonical["run_id"] != prepared.commit["run_id"]
                    or canonical["batch_id"] != prepared.commit["batch_id"]):
                raise ValueError("Rule decision readback committed scope differs")
            verified.append((canonical["record_id"], row["content_hash"]))
        identities[name] = tuple(verified)
    verify_commit_v4(prepared.commit, prepared.family_seals, detail_identities=identities)

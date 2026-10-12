"""Prepare staged decision families under the shared V4 commit seal.

No INSERT, registration or Keeper operation occurs here. The registered writer
must independently prove source and financial authority before publication.
"""
from dataclasses import dataclass
from types import MappingProxyType

from .arte_journal_commit_v4 import prepare_commit_v4, verify_commit_v4
from .arte_journal_writer import _sealed_families, _v4_family_table
from .rule_decision_basis_projection import RuleDecisionBasisBatch
from .rule_decision_basis_schema import BASIS, INTENT


@dataclass(frozen=True, slots=True)
class PreparedRuleDecisionCommit:
    commit: object
    family_seals: tuple
    families: tuple


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

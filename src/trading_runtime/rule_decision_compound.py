"""Qualified decision supplements for the shared mixed-batch writer."""
from .rule_decision_basis_projection import rekey_rule_decision_basis
from .rule_decision_basis_schema import BASIS, INTENT
from .rule_decision_publication import QualifiedRuleDecisionBatch, _check_rule_decision_qualification


def qualified_compound_rule_families(compound):
    """Validate retained native proofs and return exact rekeyed scalar families.

    This preparation performs no writes. The caller must publish these families
    with the ordinary families under one shared commit and ownership fence.
    """
    from .arte_journal_compound_v4 import V4CompoundBatch
    if type(compound) is not V4CompoundBatch:
        raise TypeError("Rule supplements require the shared compound envelope")
    parents, children = [], []
    for unit in compound.units:
        if type(unit) is not QualifiedRuleDecisionBatch:
            continue
        _check_rule_decision_qualification(unit.unit, unit.qualification)
        parent, intents = rekey_rule_decision_basis(
            unit.unit.basis, unit.unit.intents, batch_id=compound.base.batch_id)
        parents.append(parent)
        children.extend(intents)
    for key, rows in (("rule_decision_bases", parents), ("rule_decision_intents", children)):
        expected = tuple({name: value for name, value in row.items() if name != "content_hash"}
                         for row in rows)
        if expected != compound.children[key]:
            raise ValueError("Compound rule evidence differs from its qualified source units")
    return tuple((table.name, tuple(rows)) for table, rows in
                 ((BASIS, parents), (INTENT, children)) if rows)

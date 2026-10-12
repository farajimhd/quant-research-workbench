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


def prepare_qualified_compound_families(client, compound, *, verified_prior_prefix=None,
                                       first_price_source=None):
    """Prepare one complete graph using the existing ordinary-family validators.

    Qualified envelopes are reduced to their original typed bases only after
    their supplements have been independently checked. No database mutation or
    ownership advancement occurs here.
    """
    from dataclasses import replace
    from .arte_journal_compound_v4 import prepare_compound_v4_families
    supplements = qualified_compound_rule_families(compound)
    if supplements and getattr(client, "live_v4_lease", None) is not None:
        raise ValueError("Program decision publication requires its Backtest owner")
    children = dict(compound.children)
    children["rule_decision_bases"] = ()
    children["rule_decision_intents"] = ()
    ordinary = replace(compound, children=children, units=tuple(
        unit.base if type(unit) is QualifiedRuleDecisionBatch else unit
        for unit in compound.units))
    base_families, ordinary_families = prepare_compound_v4_families(
        client, ordinary, verified_prior_prefix=verified_prior_prefix,
        first_price_source=first_price_source)
    if {name for name, _ in ordinary_families} & {name for name, _ in supplements}:
        raise ValueError("Compound preparation repeats a rule family")
    return base_families, (*ordinary_families, *supplements)

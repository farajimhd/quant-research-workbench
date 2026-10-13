"""Single-decision publication through the common V4 durability boundary."""
from .rule_decision_publication import QualifiedRuleDecisionBatch, _check_rule_decision_qualification


def publish_qualified_rule_decision(client, qualified, *, verified_prior_prefix=None,
                                    timings_ns=None):
    """Preserve a one-event tail without inventing a second event or batch."""
    from .rule_decision_writer_preflight import require_rule_decision_writer_audit
    from .arte_journal_commit_v4 import _publish_typed_batch_v4, _publish_sealed_batch_v4
    from .rule_decision_basis_schema import BASIS, INTENT
    if type(qualified) is not QualifiedRuleDecisionBatch:
        raise TypeError("Single rule publication requires its native qualified envelope")
    _check_rule_decision_qualification(qualified.unit, qualified.qualification)
    require_rule_decision_writer_audit(client)
    base_families, families = _publish_typed_batch_v4(client, qualified.base,
        _prepare_only=True, verified_prior_prefix=verified_prior_prefix)
    if {name for name, _ in families} & {BASIS.name, INTENT.name}:
        raise ValueError("Ordinary preparation repeated a decision family")
    families = (*families, (BASIS.name, (qualified.unit.basis,)))
    if qualified.unit.intents:
        families = (*families, (INTENT.name, qualified.unit.intents))
    from .rule_decision_observation import qualified_observation_rows
    families = (*families, *qualified_observation_rows(qualified.unit, qualified.qualification))
    return _publish_sealed_batch_v4(client, qualified.base, base_families, families,
        verified_prior_prefix=verified_prior_prefix, timings_ns=timings_ns)

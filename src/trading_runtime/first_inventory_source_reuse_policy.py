"""Explicit scope for issued source proofs during complete inventory reads.

This declaration grants no journal, inventory, decision or recovery reuse.
The complete loader must retain its historical-prefix verification, and any
foreign reader or inventory-budget overflow must use independent cold reads.
"""
from dataclasses import dataclass

INPUT = 'declared-first-inventory-source-reuse-bounds@1'
RULE = 'first-inventory-issued-context-source-reuse@1'
PARAMETER = 'first_inventory_source_reuse_policy'


@dataclass(frozen=True, slots=True)
class FirstInventorySourceReusePolicy:
    max_contexts: int
    initial_held: bool
    proposal: bool

    def __post_init__(self):
        if type(self.max_contexts) is not int or not 1 <= self.max_contexts <= 100000:
            raise ValueError('Explicit bounded first-inventory source scope required')
        if type(self.initial_held) is not bool or type(self.proposal) is not bool:
            raise ValueError('Explicit first-inventory operation selection required')
        if not (self.initial_held or self.proposal):
            raise ValueError('First-inventory source reuse must select an operation')

    def payload(self):
        self.__post_init__()
        return dict(max_contexts=self.max_contexts,
                    initial_held=self.initial_held, proposal=self.proposal)


def require_declared_first_inventory_source_reuse(release, policy):
    claimed = INPUT in release.input_contracts or RULE in release.rule_set_contracts
    if not claimed:
        if policy is not None:
            raise ValueError('Undeclared first-inventory source reuse policy')
        return None
    if (release.input_contracts.count(INPUT) != 1
            or release.rule_set_contracts.count(RULE) != 1
            or type(policy) is not FirstInventorySourceReusePolicy):
        raise ValueError('Paired exact first-inventory source declaration required')
    policy.__post_init__()
    from . import initial_held_recovery_reuse_policy as initial
    from . import proposal_decision_inventory_reuse_policy as proposal
    for selected, dependency in ((policy.initial_held, initial),
                                 (policy.proposal, proposal)):
        if selected and (release.input_contracts.count(dependency.INPUT) != 1
                         or release.rule_set_contracts.count(dependency.RULE) != 1):
            raise ValueError('First-inventory source reuse needs its issued operation declaration')
    return policy


def parse_declared_first_inventory_source_reuse(release, value):
    if value is None:
        return require_declared_first_inventory_source_reuse(release, None)
    if type(value) is not dict or set(value) != {'max_contexts', 'initial_held', 'proposal'}:
        raise ValueError('Canonical first-inventory source payload required')
    return require_declared_first_inventory_source_reuse(
        release, FirstInventorySourceReusePolicy(**value))

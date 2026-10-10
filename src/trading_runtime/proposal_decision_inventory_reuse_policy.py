"""Explicit bounded verified proposal-context and inventory retention only."""
from dataclasses import dataclass

INPUT = 'declared-proposal-decision-inventory-reuse-bounds@1'
RULE = 'proposal-decision-exact-context-inventory-reuse@1'
PARAMETER = 'proposal_decision_inventory_reuse_policy'

@dataclass(frozen=True, slots=True)
class ProposalDecisionInventoryReusePolicy:
    max_contexts: int
    max_inventory_entries: int
    max_inventory_rows: int
    max_inventory_bytes: int

    def __post_init__(self):
        for value, limit in ((self.max_contexts,100000),(self.max_inventory_entries,32),
                (self.max_inventory_rows,1000000),(self.max_inventory_bytes,67108864)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError('Explicit bounded proposal-decision reuse declaration required')

    def payload(self):
        self.__post_init__()
        return dict(max_contexts=self.max_contexts,max_inventory_entries=self.max_inventory_entries,
            max_inventory_rows=self.max_inventory_rows,max_inventory_bytes=self.max_inventory_bytes)

def require_declared_proposal_decision_reuse(release, policy):
    claimed = INPUT in release.input_contracts or RULE in release.rule_set_contracts
    if not claimed:
        if policy is not None: raise ValueError('Undeclared proposal-decision reuse policy')
        return None
    if release.input_contracts.count(INPUT)!=1 or release.rule_set_contracts.count(RULE)!=1 or type(policy) is not ProposalDecisionInventoryReusePolicy:
        raise ValueError('Paired exact proposal-decision reuse declaration required')
    policy.__post_init__()
    return policy

def parse_declared_proposal_decision_reuse(release, value):
    if value is None:return require_declared_proposal_decision_reuse(release,None)
    if type(value) is not dict or set(value) != {'max_contexts','max_inventory_entries','max_inventory_rows','max_inventory_bytes'}:
        raise ValueError('Canonical proposal-decision reuse payload required')
    return require_declared_proposal_decision_reuse(release,ProposalDecisionInventoryReusePolicy(**value))

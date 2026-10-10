"""Explicit bounded verification reuse for an initial held operation only."""
from dataclasses import dataclass

INPUT = 'declared-initial-held-recovery-reuse-bounds@1'
RULE = 'initial-held-exact-recovery-operation-reuse@1'
PARAMETER = 'initial_held_recovery_reuse_policy'

@dataclass(frozen=True, slots=True)
class InitialHeldRecoveryReusePolicy:
    max_contexts: int
    max_inventory_entries: int
    max_inventory_rows: int
    max_inventory_bytes: int

    def __post_init__(self):
        for value, limit in ((self.max_contexts,100000),(self.max_inventory_entries,32),
                (self.max_inventory_rows,1000000),(self.max_inventory_bytes,67108864)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError('Explicit bounded initial-held reuse declaration required')

    def payload(self):
        self.__post_init__()
        return dict(max_contexts=self.max_contexts,max_inventory_entries=self.max_inventory_entries,
            max_inventory_rows=self.max_inventory_rows,max_inventory_bytes=self.max_inventory_bytes)

def require_declared_initial_held_reuse(release, policy):
    claimed = INPUT in release.input_contracts or RULE in release.rule_set_contracts
    if not claimed:
        if policy is not None: raise ValueError('Undeclared initial-held reuse policy')
        return None
    if release.input_contracts.count(INPUT)!=1 or release.rule_set_contracts.count(RULE)!=1 or type(policy) is not InitialHeldRecoveryReusePolicy:
        raise ValueError('Paired exact initial-held reuse declaration required')
    policy.__post_init__()
    return policy

def parse_declared_initial_held_reuse(release, value):
    if value is None:return require_declared_initial_held_reuse(release,None)
    if type(value) is not dict or set(value) != {'max_contexts','max_inventory_entries','max_inventory_rows','max_inventory_bytes'}:
        raise ValueError('Canonical initial-held reuse payload required')
    return require_declared_initial_held_reuse(release,InitialHeldRecoveryReusePolicy(**value))

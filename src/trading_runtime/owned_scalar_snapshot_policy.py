"""Explicit privately owned snapshot bounds; never source admission authority."""
from dataclasses import dataclass

INPUT = 'declared-owned-scalar-snapshot-bounds@1'
RULE = 'exact-owned-scalar-row-snapshot-reuse@1'


@dataclass(frozen=True, slots=True)
class OwnedScalarSnapshotPolicy:
    max_entries: int
    max_rows: int
    max_bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in
               (self.max_entries, self.max_rows, self.max_bytes)):
            raise ValueError('Explicit positive integer owned snapshot bounds required')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, input_contract=INPUT, max_entries=self.max_entries,
            max_rows=self.max_rows, max_bytes=self.max_bytes,
            content='complete ordered scalar rows with exact types and float bits',
            ownership='private copies; immutable row views; no caller backing aliases',
            scope='owned projected-node snapshot construction only',
            fallback='unknown or evicted groups use complete ordinary snapshots',
            admission='source, request, semantic batch, full replay, cash, fills and recovery independent')


def parse_owned_scalar_snapshot_policy(value):
    if type(value) is not dict:
        raise ValueError('Complete owned snapshot declaration required')
    fields = ('max_entries', 'max_rows', 'max_bytes')
    if not set(fields).issubset(value):
        raise ValueError('Owned snapshot bounds cannot receive defaults')
    policy = OwnedScalarSnapshotPolicy(*(value[key] for key in fields))
    expected = policy.payload()
    if (set(value) != set(expected) or any(type(k) is not str for k in value)
            or any(type(value[k]) is not type(v) or value[k] != v for k, v in expected.items())):
        raise ValueError('Owned snapshot semantics differ')
    return policy


def declared_owned_scalar_snapshot_policy(release, value):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Exact immutable release required')
    release.verify()
    inputs, rules = release.input_contracts.count(INPUT), release.rule_set_contracts.count(RULE)
    if not inputs and not rules and value is None:
        return None
    if inputs != 1 or rules != 1:
        raise ValueError('Owned snapshots require exactly paired declarations')
    return parse_owned_scalar_snapshot_policy(value)

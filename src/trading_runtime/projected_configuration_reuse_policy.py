"""Explicit pure node-projection reuse declaration; no admission capability."""
from dataclasses import dataclass

INPUT = 'declared-projected-configuration-reuse-bounds@1'
RULE = 'exact-projected-configuration-nodes-reuse@1'


@dataclass(frozen=True, slots=True)
class ProjectedConfigurationReusePolicy:
    max_entries: int
    max_input_bytes: int
    max_rows: int
    max_bytes: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in
               (self.max_entries, self.max_input_bytes, self.max_rows, self.max_bytes)):
            raise ValueError('Explicit positive integer projected-node reuse bounds required')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, input_contract=INPUT, max_entries=self.max_entries,
            max_input_bytes=self.max_input_bytes, max_rows=self.max_rows, max_bytes=self.max_bytes,
            content='complete canonical common identity and ordered parent/selected/proposal trees',
            scope='deterministic encoding, node UUID generation and scalar/hash sealing only',
            ownership='owned immutable scalar rows; no caller mapping aliases',
            eviction='bounded least recently used; oversized valid outputs complete and uncached',
            admission='issued source, request, semantic batch and complete replay equality checked independently')


def parse_projected_configuration_reuse_policy(value):
    if type(value) is not dict:
        raise ValueError('Complete projected-node reuse declaration required')
    fields = ('max_entries', 'max_input_bytes', 'max_rows', 'max_bytes')
    if not set(fields).issubset(value):
        raise ValueError('Projected-node reuse bounds cannot receive defaults')
    policy = ProjectedConfigurationReusePolicy(*(value[field] for field in fields))
    expected = policy.payload()
    if (set(value) != set(expected) or any(type(key) is not str for key in value)
            or any(type(value[key]) is not type(scalar) or value[key] != scalar
                   for key, scalar in expected.items())):
        raise ValueError('Projected-node reuse semantics differ')
    return policy


def declared_projected_configuration_reuse_policy(release, value):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Exact immutable release required for projected-node reuse')
    release.verify()
    inputs, rules = release.input_contracts.count(INPUT), release.rule_set_contracts.count(RULE)
    if not inputs and not rules and value is None:
        return None
    if inputs != 1 or rules != 1:
        raise ValueError('Projected-node reuse requires exactly paired declarations')
    return parse_projected_configuration_reuse_policy(value)

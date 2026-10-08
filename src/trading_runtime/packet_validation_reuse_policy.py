"""Explicit immutable pure-content reuse declaration; no runtime admission."""
from dataclasses import dataclass

RULE = 'exact-scalar-packet-validation-reuse@1'
INPUT = 'declared-pure-packet-validation-bounds@1'


@dataclass(frozen=True, slots=True)
class PacketValidationReusePolicy:
    max_entries: int
    max_rows: int
    max_bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in
               (self.max_entries, self.max_rows, self.max_bytes)):
            raise ValueError('Explicit positive integer validation reuse bounds required')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, input_contract=INPUT, max_entries=self.max_entries,
                    max_rows=self.max_rows, max_bytes=self.max_bytes,
                    content='all ordered scalar rows; exact scalar types and Float64 bits',
                    scope='successful pure packet content validation only',
                    mutation='snapshot before and after; changed contents fail',
                    eviction='bounded least recently used; oversized packets are not retained',
                    admission='source, ownership, financial and recovery checks execute independently')


def parse_packet_validation_reuse_policy(value):
    if type(value) is not dict:
        raise ValueError('Complete validation reuse policy required')
    required = ('max_entries', 'max_rows', 'max_bytes')
    if not set(required).issubset(value):
        raise ValueError('Validation reuse bounds cannot receive defaults')
    policy = PacketValidationReusePolicy(*(value[name] for name in required))
    expected = policy.payload()
    if (set(value) != set(expected) or any(type(key) is not str for key in value) or
            any(type(value[key]) is not type(scalar) or value[key] != scalar
                for key, scalar in expected.items())):
        raise ValueError('Validation reuse semantic declaration differs')
    return policy


def declared_packet_validation_reuse_policy(release, value):
    """Require paired selection; parsing is not a source/release certificate."""
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Exact immutable release required for validation reuse')
    release.verify()
    inputs, rules = release.input_contracts.count(INPUT), release.rule_set_contracts.count(RULE)
    if not inputs and not rules and value is None:
        return None
    if inputs != 1 or rules != 1:
        raise ValueError('Validation reuse requires exactly paired input and rule declarations')
    return parse_packet_validation_reuse_policy(value)

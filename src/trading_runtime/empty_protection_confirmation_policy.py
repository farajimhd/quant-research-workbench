"""Unselected declaration for commandless confirmation; no source authority."""
from dataclasses import dataclass

INPUT = 'declared-empty-protection-confirmation-input@1'
RULE = 'declared-empty-protection-confirmation-rule@1'


@dataclass(frozen=True, slots=True)
class EmptyProtectionConfirmationPolicy:
    schema_version: int

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Exact empty protection confirmation schema required')

    def payload(self):
        self.__post_init__()
        return dict(schema_version=self.schema_version, input_contract=INPUT, rule=RULE,
            eligibility='exact issued request; zero commands; no recovery context',
            omitted='unused effective protection acknowledgement history read only',
            preserved='fresh installed source, writer fence, committed prefix, live legs, residual lots, stop ceiling and financial quantity checks',
            fallback='nonempty commands, unresolved recovery and unselected releases retain full history read',
            authority='declaration only; installed source and native owner must independently admit execution')


def parse_empty_protection_confirmation_policy(value):
    if type(value) is not dict or 'schema_version' not in value:
        raise ValueError('Complete empty protection confirmation declaration required')
    result = EmptyProtectionConfirmationPolicy(value['schema_version'])
    expected = result.payload()
    if (set(value) != set(expected) or any(type(k) is not str for k in value) or
            any(type(value[k]) is not type(v) or value[k] != v for k, v in expected.items())):
        raise ValueError('Empty protection confirmation semantics differ')
    return result


def declared_empty_protection_confirmation_policy(release, value):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Exact immutable release required')
    release.verify()
    inputs = release.input_contracts.count(INPUT)
    rules = release.rule_set_contracts.count(RULE)
    if not inputs and not rules and value is None:
        return None
    if inputs != 1 or rules != 1:
        raise ValueError('Exactly paired empty confirmation input and rule required')
    return parse_empty_protection_confirmation_policy(value)


def requires_protection_ack_history(policy, *, command_count, recovery_pending):
    """Pure eligibility only; this cannot certify the request or its prefix."""
    if (policy is not None and type(policy) is not EmptyProtectionConfirmationPolicy or
            type(command_count) is not int or command_count < 0 or
            type(recovery_pending) is not bool):
        raise ValueError('Exact policy, command count and recovery state required')
    if policy is None:
        return True
    policy.__post_init__()
    return command_count != 0 or recovery_pending

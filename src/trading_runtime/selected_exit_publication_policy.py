"""Explicit declaration for selected-entry exit publication context."""
from dataclasses import dataclass

INPUT = 'declared-selected-exit-publication-input@1'
RULE = 'declared-selected-exit-publication-rule@1'


@dataclass(frozen=True, slots=True)
class SelectedExitPublicationPolicy:
    schema_version: int

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Exact selected exit publication schema required')

    def payload(self):
        self.__post_init__()
        return dict(
            schema_version=self.schema_version, input_contract=INPUT, rule=RULE,
            context='exact installed source, verified committed predecessor and identical price authority',
            children='selected-entry followthrough, profit giveback, confirmed AH and liquidity failure',
            publication='existing typed writer context-bearing lane; no caller network IO',
            verification='retain source ancestry, issued entry ownership, completed clocks and typed child validation',
            missing='fail closed; no inferred source, price replacement or generic-entry fallback',
            compatibility='unselected releases retain their original routing',
        )


def parse_selected_exit_publication_policy(value):
    if type(value) is not dict or 'schema_version' not in value:
        raise ValueError('Complete selected exit publication declaration required')
    policy = SelectedExitPublicationPolicy(value['schema_version'])
    expected = policy.payload()
    if (set(value) != set(expected) or
            any(type(key) is not str for key in value) or
            any(type(value[key]) is not type(item) or value[key] != item
                for key, item in expected.items())):
        raise ValueError('Selected exit publication semantics differ')
    return policy


def declared_selected_exit_publication_policy(release, value):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Exact immutable release required')
    release.verify()
    inputs = release.input_contracts.count(INPUT)
    rules = release.rule_set_contracts.count(RULE)
    if not inputs and not rules and value is None:
        return None
    if inputs != 1 or rules != 1:
        raise ValueError('Exactly paired selected exit input and rule required')
    return parse_selected_exit_publication_policy(value)

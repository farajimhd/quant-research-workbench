from copy import deepcopy
from dataclasses import replace
import json
import ast
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest

from src.trading_runtime.declared_early_original_risk_policy import INPUT_CONTRACT, POLICY_KEY, parse_declared_early_original_risk_policy
from src.trading_runtime.early_original_risk_failure import EarlyOriginalRiskPolicy
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_fifty_seven_release import release_contract as parent_release
from src.trading_runtime.numbered_fixed_strategy import DECLARED_FIXED_ADAPTER, DeclaredFixedStrategyContract


def declaration():
    policy = EarlyOriginalRiskPolicy('declared-first-minute-test@1', (1, 4), (1, 4),
                                    signal_reference_fraction_bounds=((0, 1), (1, 50)))
    parent = parent_release()
    draft = replace(parent, number=901, executor_revision=901,
                    input_contracts=(*parent.input_contracts, DECLARED_FIXED_ADAPTER, INPUT_CONTRACT),
                    rule_set_contracts=(*parent.rule_set_contracts, policy.policy_id), approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    return release, policy, {POLICY_KEY: json.loads(canonical_json(policy.payload()))}


def test_exact_policy_is_selected_without_strategy_number_branch():
    release, policy, payload = declaration()
    assert parse_declared_early_original_risk_policy(release, payload) == policy
    draft = replace(release, number=902, executor_revision=902, approved_digest='')
    other = replace(draft, approved_digest=draft.digest())
    assert parse_declared_early_original_risk_policy(other, payload) == policy


def test_undeclared_parent_retains_no_additional_policy():
    assert parse_declared_early_original_risk_policy(parent_release(), {}) is None


def test_actual_declared_contract_selects_policy_and_preserves_parent_economics():
    from src.trading_runtime import strategy_fifty_seven_release as parent
    release, policy, payload = declaration()
    policies = {**parent.INHERITED_POLICIES,
                'half_risk_liquidity_policy': parent.HALF_RISK_LIQUIDITY_POLICY,
                'entry_spread_risk_policy': parent.ENTRY_SPREAD_RISK_POLICY_PAYLOAD,
                **payload}
    contract = DeclaredFixedStrategyContract(release.number, release.executor_strategy_id,
        release.evaluation_interval, release, canonical_json(policies))
    assert contract.early_original_risk_policy == policy
    assert contract.entry_spread_risk_policy == parent.ENTRY_SPREAD_RISK_POLICY
    assert contract.allows_adds is False
    assert contract.allows_completed_30s_trailing is False


def test_existing_legacy_and_declared_contracts_keep_their_policies():
    from src.trading_runtime.strategy_fifty_seven_contract import strategy_fifty_seven_contract
    from src.trading_runtime.strategy_seventy_four_contract import strategy_seventy_four_contract
    from src.trading_runtime.strategy_fifty_seven_release import EARLY_FAILURE_POLICY
    assert strategy_fifty_seven_contract().early_original_risk_policy == EARLY_FAILURE_POLICY
    assert strategy_seventy_four_contract().early_original_risk_policy is None


@pytest.mark.parametrize('field,value', [('premarket_fraction', [True, 4]),
    ('eligibility_ms', True), ('quote_max_age_us', 1000001),
    ('priority', 'before_inherited_exits'), ('source', 'forming_bar')])
def test_foreign_scalars_or_execution_semantics_are_rejected(field, value):
    release, _, payload = declaration()
    payload = deepcopy(payload)
    payload[POLICY_KEY][field] = value
    with pytest.raises(ValueError):
        parse_declared_early_original_risk_policy(release, payload)


def test_missing_or_duplicate_input_and_missing_rule_are_rejected():
    release, policy, payload = declaration()
    variants = [replace(release, input_contracts=tuple(x for x in release.input_contracts if x != INPUT_CONTRACT)),
                replace(release, input_contracts=(*release.input_contracts, INPUT_CONTRACT)),
                replace(release, rule_set_contracts=tuple(x for x in release.rule_set_contracts if x != policy.policy_id))]
    for draft in variants:
        draft = replace(draft, approved_digest='')
        draft = replace(draft, approved_digest=draft.digest())
        with pytest.raises(ValueError):
            parse_declared_early_original_risk_policy(draft, payload)
    with pytest.raises(ValueError):
        parse_declared_early_original_risk_policy(release, {})


def test_complete_parent_module_restoration_rejects_foreign_delta():
    from src.backend.backtest_declared_early_risk_compatibility import restore_reviewed_parent_source
    root = Path(__file__).resolve().parents[1]
    relative = 'src/trading_runtime/numbered_fixed_strategy.py'
    current = (root / relative).read_text(encoding='utf-8')
    parent = subprocess.check_output(['git', 'show', '90684f231:' + relative], cwd=root).decode('utf-8')
    digest = lambda source: sha256(ast.unparse(ast.parse(source)).encode()).hexdigest()
    restored = restore_reviewed_parent_source(current, relative)
    assert digest(restored) == digest(parent)
    foreign = current + '\n_FOREIGN_EXECUTION_DELTA = True\n'
    assert restore_reviewed_parent_source(foreign, relative) == foreign
    assert digest(foreign) != digest(parent)
    assert restore_reviewed_parent_source(current, 'src/unrelated.py') == current

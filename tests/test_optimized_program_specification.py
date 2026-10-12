"""Frozen programme compilation through the shared normalized release codec."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.trading_runtime import optimized_program_specification as compiler
from src.trading_runtime.columnar_rule_program import InputField, Operation
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from src.trading_runtime.optimized_strategy_reference import OptimizationReference
from tests.test_columnar_strategy_release import bundle


def source(monkeypatch):
    spec, _, kwargs = bundle()
    policy = dict(spec.policy)
    policy["cooldown"] = policy.pop("cooldown_seconds")
    def rule(feature):
        return dict(output=2, nodes=[
            dict(op=int(Operation.FEATURE), a=-1, b=-1, feature=feature, window=1, value=0., unit="ratio"),
            dict(op=int(Operation.CONSTANT), a=-1, b=-1, feature=0, window=1, value=0., unit="ratio"),
            dict(op=int(Operation.GREATER), a=0, b=1, feature=0, window=1, value=0., unit="ratio")])
    program = dict(policy=policy, rules={stage: rule(677) for stage in compiler.STAGES},
        open_rules={stage: rule(698) for stage in compiler.STAGES}, minimum_age={"exit": 10})
    monkeypatch.setattr(compiler, "WINNER_SEMANTIC_HASH",
        sha256(json.dumps(program, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    fields = {name: InputField(name, unit, "arte.qmd_causal_history_value_v1", "a" * 64, 1000)
              for name, unit in compiler.WINNER_FIELDS.values()}
    fields[compiler.STOP_FIELD] = InputField(compiler.STOP_FIELD, "price",
        "arte.qmd_causal_history_value_v1", "a" * 64, 1000)
    reference = OptimizationReference(24, compiler.WINNER_SHA256, 872, program,
        dict(entry_dollars=1000., add_dollars=200., fee_per_share=.005, minimum_order_fee=1., cost_bps=0.), {})
    universe = dict(version="v6-volume-market-blocks-v1", window=30, top_n=10, minimum_close=.8, maximum_close=50)
    return reference, fields, universe, kwargs


def test_winner_compiles_open_rules_and_elapsed_cooldown_into_shared_release(monkeypatch):
    reference, fields, universe, kwargs = source(monkeypatch)
    specification = compiler.compile_frozen_winner(reference, fields, universe_identity=universe)
    assert specification.policy["cooldown_seconds"] == 175
    assert "cooldown" not in specification.policy
    assert reference.program["policy"]["cooldown"] == 175  # Source is preserved.
    assert specification.programs["entry"].nodes[0].field == compiler.WINNER_FIELDS[677][0]
    assert specification.programs["exit"].nodes[0].field == compiler.WINNER_FIELDS[698][0]
    release = restore_release(project_release(specification, fields, **kwargs))
    assert release.programs == specification.programs
    assert release.header["entry_cash"] == 1000 and release.header["add_cash"] == 200
    assert release.header["cooldown_seconds"] == 175
    assert release.minimum_age_seconds["exit"] == 10
    assert specification.commission_per_share == .005 and specification.minimum_commission == 1.


def test_unknown_or_ambiguous_policy_fields_fail_before_release(monkeypatch):
    reference, fields, universe, _ = source(monkeypatch)
    program = deepcopy(reference.program)
    program["policy"]["cooldown_seconds"] = 175
    monkeypatch.setattr(compiler, "WINNER_SEMANTIC_HASH",
        sha256(json.dumps(program, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    with pytest.raises(ValueError, match="optimization contract"):
        compiler.compile_frozen_winner(replace(reference, program=program), fields, universe_identity=universe)

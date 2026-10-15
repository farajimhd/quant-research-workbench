"""V19 operation contracts through the shared runnable rule evaluator."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import torch

from research.vectorized_backtest.v19.program import Node, Program, TorchPrograms
from src.trading_runtime.columnar_rule_program import (
    InputField, Operation as Op, RuleNode as N, RuleProgram, evaluate_rules,
)


FIELD = InputField("history.momentum_close.21.relative_to_close", "ratio",
                   "arte.history_feature_v1", "a" * 64, 1000)
FIELDS = {FIELD.name: FIELD}


def frame(values, known, listing="a", block=0):
    times = [datetime(2026, 8, 3, tzinfo=timezone.utc) + timedelta(seconds=i)
             for i in range(len(values))]
    return pl.DataFrame({"listing_id": [listing] * len(values),
        "evaluation_block": [block] * len(values), "observed_ordinal": range(len(values)),
        "available_at": times, "boundary_at": times,
        FIELD.name: values.tolist(), FIELD.name + "__valid": known.tolist()})


@pytest.mark.parametrize("window", [1, 2, 3, 5, 10, 20, 30, 60, 120])
@pytest.mark.parametrize("boolean_source", [False, True])
def test_v19_operations_match_torch_outputs_and_masks(window, boolean_source):
    values = np.random.default_rng(223).normal(size=257).astype(np.float32)
    values[7], values[8] = np.nan, np.inf
    known = np.ones(values.size, dtype=bool)
    known[19:21] = False
    nodes = [N(Op.FEATURE, field=FIELD.name), N(Op.CONSTANT)]
    reference = [Node(0, feature=0), Node(1)]
    if boolean_source:
        nodes += [N(Op.GREATER, left=0, right=1), N(Op.BOOL_RATIO, left=2)]
        reference += [Node(6, a=0, b=1), Node(21, a=2)]
    source = len(nodes) - 1 if boolean_source else 0
    nodes += [N(Op.STD, left=source, window=window)]
    reference += [Node(23, a=source, window=window)]
    std = len(nodes) - 1
    nodes += [N(Op.CONSTANT, literal=.25), N(Op.GREATER, left=std, right=std + 1)]
    reference += [Node(1, value=.25), Node(6, a=std, b=std + 1)]
    output = len(nodes) - 1
    rule = RuleProgram(tuple(nodes), output)
    expected, masks = TorchPrograms([Program(tuple(reference), output)],
        [SimpleNamespace(unit="ratio")])(
            torch.from_numpy(values[:, None]), torch.from_numpy(known[:, None]))
    original = frame(values, known)
    actual = evaluate_rules(original, {"entry": rule}, FIELDS)
    assert actual["entry"].to_list() == ((expected[0] != 0) & masks[0]).tolist()
    assert actual["entry__valid"].to_list() == masks[0].tolist()
    prefix = evaluate_rules(original.head(128), {"entry": rule}, FIELDS)
    assert prefix.equals(actual.head(128))
    grouped = pl.concat([original, frame(values, known, block=1),
                         frame(values, known, listing="b")])
    result = evaluate_rules(grouped, {"entry": rule}, FIELDS)
    assert result["entry"].to_list() == actual["entry"].to_list() * 3
    assert result["entry__valid"].to_list() == actual["entry__valid"].to_list() * 3


def test_population_std_and_boolean_conversion_keep_missing_evidence_invalid():
    # Population STD([0, 2]) == 1, whereas sample STD is sqrt(2).
    rule = RuleProgram((N(Op.FEATURE, field=FIELD.name), N(Op.STD, left=0, window=2),
        N(Op.CONSTANT, literal=1), N(Op.LESS_EQUAL, left=1, right=2),
        N(Op.BOOL_RATIO, left=3), N(Op.CONSTANT),
        N(Op.GREATER, left=4, right=5)), 6)
    result = evaluate_rules(frame(np.array([0., 2., 0., 0.]),
        np.array([True, True, False, True])), {"entry": rule}, FIELDS)
    assert result["entry__valid"].to_list() == [False, True, False, False]
    assert result["entry"].to_list() == [False, True, False, False]


def test_projected_fields_keep_full_shared_catalog_literal_units():
    rule = RuleProgram((N(Op.CONSTANT, literal=1, unit="bool"),
                        N(Op.CONSTANT, unit="log_price")), 0)
    rule.validate(FIELDS)
    with pytest.raises(ValueError, match="Unknown rule literal unit"):
        RuleProgram((N(Op.CONSTANT, literal=1, unit="bool"),
                     N(Op.CONSTANT, unit="unknown_unit")), 0).validate(FIELDS)


@pytest.mark.parametrize("operation,unit", [(Op.BOOL_RATIO, "ratio"), (Op.STD, "bool")])
def test_v19_operations_reject_wrong_operand_units(operation, unit):
    rule = RuleProgram((N(Op.CONSTANT, unit=unit), N(operation, left=0)), 1)
    with pytest.raises(ValueError, match="requires bool|Numeric"):
        rule.validate(FIELDS)


def test_context_bound_counts_119_preceding_rows_and_validates_dead_nodes():
    allowed = (N(Op.FEATURE, field=FIELD.name), N(Op.STD, left=0, window=120),
               N(Op.CONSTANT), N(Op.GREATER, left=1, right=2))
    RuleProgram(allowed, 3).validate(FIELDS)
    # A lag after the 120-row STD needs 120 preceding rows and is rejected,
    # even if unreachable from the selected output, matching V19 validation.
    with pytest.raises(ValueError, match="119-row"):
        RuleProgram(allowed + (N(Op.LAG, left=1, window=1),), 3).validate(FIELDS)
    with pytest.raises(ValueError, match="120 observed"):
        Program((Node(0, feature=0), Node(23, a=0, window=120), Node(1),
                 Node(6, a=1, b=2), Node(13, a=1)), 3).validate(
                     [SimpleNamespace(unit="ratio")])

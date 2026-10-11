from dataclasses import replace
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.trading_runtime.columnar_rule_program import (
    InputField, Operation as Op, RuleNode as N, RuleProgram, evaluate_rules,
)
from src.trading_runtime.columnar_strategy_schema import TABLES


FIELD = InputField("history.momentum_close.21.relative_to_close", "ratio",
                   "arte.history_feature_v1", "a" * 64, 1000)


def frame(values, valid=None):
    times = [datetime(2026, 8, 3, 14, tzinfo=timezone.utc) + timedelta(seconds=i)
             for i in range(len(values))]
    return pl.DataFrame({"listing_id": ["listing-a"] * len(values),
        "evaluation_block": [0] * len(values), "observed_ordinal": range(len(values)),
        "available_at": times, "boundary_at": times,
        FIELD.name: values, FIELD.name + "__valid": valid or [True] * len(values)})


def comparison(op=Op.GREATER):
    return RuleProgram((N(Op.FEATURE, field=FIELD.name),
                        N(Op.CONSTANT, literal=0), N(op, left=0, right=1)), 2)


def test_missing_is_not_true_through_not_or():
    p = RuleProgram(comparison().nodes + (N(Op.NOT, left=2),
        N(Op.CONSTANT, literal=1, unit="bool"), N(Op.OR, left=3, right=4)), 5)
    result = evaluate_rules(frame([1., -1., 0.], [True, False, True]), {"entry": p}, {FIELD.name: FIELD})
    assert result["entry"].to_list() == [True, False, True]
    assert result["entry__valid"].to_list() == [True, False, True]


def test_temporal_rules_are_prefix_independent_and_do_not_cross_listings():
    p = RuleProgram((N(Op.FEATURE, field=FIELD.name), N(Op.DIFFERENCE, left=0, window=2),
                     N(Op.CONSTANT), N(Op.GREATER, left=1, right=2)), 3)
    original = frame([1., 2., 4., 0., 9.])
    short = evaluate_rules(original.head(3), {"entry": p}, {FIELD.name: FIELD})
    full = evaluate_rules(original, {"entry": p}, {FIELD.name: FIELD})
    assert short.equals(full.head(3))
    assert full["entry"].to_list() == [False, False, True, False, True]
    second = original.with_columns(pl.lit("listing-b").alias("listing_id"))
    together = evaluate_rules(pl.concat([original, second]), {"entry": p}, {FIELD.name: FIELD})
    assert together["entry"].to_list() == full["entry"].to_list() * 2


def test_mean_mask_and_float32_values():
    p = RuleProgram((N(Op.FEATURE, field=FIELD.name), N(Op.MEAN, left=0, window=2),
                     N(Op.CONSTANT, literal=2), N(Op.GREATER, left=1, right=2)), 3)
    actual = evaluate_rules(frame([1., 5., 7.], [True, True, False]), {"entry": p}, {FIELD.name: FIELD})
    assert actual["entry"].to_list() == [False, True, False]
    assert actual["entry__valid"].to_list() == [False, True, False]


def test_future_and_missing_observed_rows_fail_closed():
    f = frame([1., 2.])
    with pytest.raises(ValueError, match="future"):
        evaluate_rules(f.with_columns((pl.col("available_at") + pl.duration(seconds=1))),
                       {"entry": comparison()}, {FIELD.name: FIELD})
    with pytest.raises(ValueError, match="unordered"):
        evaluate_rules(f.with_columns(pl.Series("observed_ordinal", [0, 2])),
                       {"entry": comparison()}, {FIELD.name: FIELD})


@pytest.mark.parametrize("name", ["position.entry_price", "market.unrealized_pnl", "market.average_cost"])
def test_execution_fields_are_forbidden(name):
    with pytest.raises(ValueError, match="execution-derived"):
        replace(FIELD, name=name)


def test_shared_schemas_have_explicit_scalar_columns_only():
    assert len({t.name for t in TABLES}) == len(TABLES)
    for table in TABLES:
        assert "live_market_ssd" in table.ddl()
        assert not any("payload" in name or "json" in name for name, _ in table.columns)
        assert not any(kind.startswith(("JSON", "Map", "Array", "Tuple", "Object")) for _, kind in table.columns)


@pytest.mark.parametrize("operation", [Op.ADD, Op.SUBTRACT, Op.MULTIPLY, Op.DIVIDE,
    Op.GREATER, Op.GREATER_EQUAL, Op.LESS, Op.LESS_EQUAL, Op.LAG, Op.MEAN,
    Op.MINIMUM, Op.MAXIMUM, Op.DIFFERENCE, Op.CROSS_ABOVE, Op.CROSS_BELOW, Op.ABS])
def test_rule_outputs_and_masks_match_search_evaluator(operation):
    import numpy as np
    import torch
    from types import SimpleNamespace
    from research.vectorized_backtest.v6.torch_backtest.program import Node, Program, TorchPrograms

    values = np.random.default_rng(2237).normal(size=256).astype(np.float32)
    known = np.ones(256, dtype=bool)
    known[17:19] = False
    unary = operation in (Op.LAG, Op.MEAN, Op.MINIMUM, Op.MAXIMUM, Op.DIFFERENCE, Op.ABS)
    bool_output = operation in (Op.GREATER, Op.GREATER_EQUAL, Op.LESS, Op.LESS_EQUAL,
                                Op.CROSS_ABOVE, Op.CROSS_BELOW)
    source_nodes = [Node(0, feature=0), Node(1, value=.25),
                    Node(operation, a=0, b=-1 if unary else 1, window=2)]
    nodes = [N(Op.FEATURE, field=FIELD.name), N(Op.CONSTANT, literal=.25),
             N(operation, left=0, right=-1 if unary else 1, window=2)]
    if not bool_output:
        source_nodes.extend((Node(1, value=0), Node(6, a=2, b=3)))
        nodes.extend((N(Op.CONSTANT), N(Op.GREATER, left=2, right=3)))
    output = len(nodes) - 1
    expected, valid = TorchPrograms([Program(tuple(source_nodes), output)],
        [SimpleNamespace(unit="ratio")])(torch.from_numpy(values[:, None]), torch.from_numpy(known[:, None]))
    actual = evaluate_rules(frame(values.tolist(), known.tolist()),
        {"entry": RuleProgram(tuple(nodes), output)}, {FIELD.name: FIELD})
    assert actual["entry"].to_list() == ((expected[0] != 0) & valid[0]).tolist()
    assert actual["entry__valid"].to_list() == valid[0].tolist()

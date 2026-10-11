from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest

from src.market_engine import columnar_history_product as p
from src.market_engine.causal_market_history import prepare_causal_history
from src.market_engine.causal_volume_history import elapsed_volume_history
from src.trading_runtime.optimized_program_specification import WINNER_FIELDS, STOP_FIELD
from src.trading_runtime.columnar_rule_program import InputField, Operation, RuleNode, RuleProgram, evaluate_rules


def inputs(n=200):
    fields = tuple(sorted([p.BY_NAME[name] for name, _ in WINNER_FIELDS.values()] +
                          [p.BY_NAME[STOP_FIELD]], key=lambda f: f.field_id))
    scope = p.HistoryScope("build", date(2026, 8, 3), "listing", "ABC",
        "00000000-0000-0000-0000-000000000001", p.product_revision(fields, "a" * 64), 34200, 34200 + n)
    rng = np.random.default_rng(223)
    indices = np.flatnonzero(rng.uniform(size=n) > .2)
    indices = indices[indices >= 3]
    price = 100000 + rng.integers(-10000, 10000, size=len(indices))
    bars = pl.DataFrame(dict(bucket_index=34200 + indices, price_valid=np.ones(len(indices), dtype=np.uint8),
        extremes_valid=(indices % 7 != 0).astype(np.uint8), close_int=price,
        high_int=price + 100, low_int=price - 100, volume=np.full(len(indices), 10.)))
    # Quotes appear before the first last-price observation. Executable activity
    # deliberately differs from total volume/notional to catch source substitution.
    liquidity = pl.DataFrame(dict(bucket_index=np.arange(34201, 34200 + n),
                                  execution_notional=np.full(n - 1, 23.)))
    return scope, bars, liquidity, fields


def test_catalog_preserves_exact_frozen_feature_ids_and_units():
    assert len(p.CATALOG) == 604
    for field_id, (name, unit) in WINNER_FIELDS.items():
        assert p.BY_NAME[name] == p.HistoryField(field_id, name, unit)
    assert p.BY_NAME[STOP_FIELD].unit == "price"


def test_normalized_interval_product_reconstructs_exact_dense_float32_inputs():
    scope, bars, liquidity, fields = inputs()
    prepared = p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                                 producer_code_hash="a" * 64)
    dense = p.prepare_dense_inputs(scope, bars, liquidity)
    expected = prepare_causal_history(dense, tuple(f.name for f in fields))
    actual = p.expand_values(prepared, first_ordinal=0, stop_ordinal=scope.clock_count)
    for field in fields:
        valid = expected[field.name + "__valid"].to_numpy()
        result = actual[field.name].to_numpy()
        values = expected[field.name].cast(pl.Float32).to_numpy()
        np.testing.assert_array_equal(actual[field.name + "__valid"].to_numpy(), valid)
        np.testing.assert_array_equal(result[valid].view(np.uint32), values[valid].view(np.uint32))
        assert np.isnan(result[~valid]).all()
    assert prepared.clocks["execution_notional"][1] == 23.
    assert prepared.clocks["share_volume"][1] == 0.
    assert prepared.clocks["source_known"].head(3).to_list() == [0, 1, 1]
    np.testing.assert_array_equal(prepared.clocks["rolling_volume"].to_numpy(),
        elapsed_volume_history(dense, 30)["rolling_volume"].to_numpy())
    assert actual["observed_ordinal"].to_list() == list(range(scope.clock_count))


def test_window_read_does_not_reset_history_or_change_boundary_values():
    scope, bars, liquidity, fields = inputs()
    prepared = p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                                 producer_code_hash="a" * 64)
    whole = p.expand_values(prepared, first_ordinal=0, stop_ordinal=scope.clock_count)
    tail = p.expand_values(prepared, first_ordinal=100, stop_ordinal=150)
    assert tail.equals(whole.slice(100, 50))


def test_interval_gaps_overlaps_and_invalid_value_masks_fail_closed():
    scope, bars, liquidity, fields = inputs()
    prepared = p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                                 producer_code_hash="a" * 64)
    gap = prepared.values.with_columns(pl.when(pl.int_range(pl.len()) == 0).then(1)
        .otherwise(pl.col("first_ordinal")).cast(pl.UInt32).alias("first_ordinal"))
    with pytest.raises(ValueError, match="coverage"):
        p.expand_values(replace(prepared, values=gap), first_ordinal=0, stop_ordinal=50)
    with pytest.raises(ValueError, match="coverage"):
        p.expand_values(replace(prepared, values=pl.concat([prepared.values, prepared.values.head(1)])),
                        first_ordinal=0, stop_ordinal=50)
    bad = prepared.values.with_columns(pl.when(pl.col("valid") == 0).then(5.)
                                       .otherwise(pl.col("value")).alias("value"))
    with pytest.raises(ValueError, match="validity"):
        p.expand_values(replace(prepared, values=bad), first_ordinal=0, stop_ordinal=50)


def test_parent_duplicates_unknown_flags_and_wrong_scopes_fail_closed():
    scope, bars, liquidity, fields = inputs()
    for bad in (pl.concat([bars, bars.head(1)]),
                bars.with_columns(pl.lit(2).alias("price_valid")),
                bars.with_columns(pl.lit(scope.stop_bucket).alias("bucket_index"))):
        with pytest.raises(ValueError):
            p.prepare_product(scope, bad, liquidity, fields, volume_window_seconds=30,
                              producer_code_hash="a" * 64)
    with pytest.raises(ValueError, match="revision"):
        p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                          producer_code_hash="b" * 64)


def test_future_tail_is_independent_of_an_earlier_field_prefix():
    scope, bars, liquidity, fields = inputs()
    prepared = p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                                 producer_code_hash="a" * 64)
    shorter = replace(scope, stop_bucket=scope.first_bucket + 100)
    prefix = p.prepare_product(shorter, bars.filter(pl.col("bucket_index") < shorter.stop_bucket),
        liquidity.filter(pl.col("bucket_index") < shorter.stop_bucket), fields,
        volume_window_seconds=30, producer_code_hash="a" * 64)
    assert p.expand_values(prepared, first_ordinal=0, stop_ordinal=100).equals(
        p.expand_values(prefix, first_ordinal=0, stop_ordinal=100))


def test_product_schema_is_central_scalar_and_explicit_ssd():
    for table in p.TABLES:
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert not any(token in table.ddl().lower() for token in ("json", "blob", "payload", "array("))


def test_consumer_returns_completed_clocks_and_rejects_corruption_outside_window():
    scope, bars, liquidity, fields = inputs()
    prepared = p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                                 producer_code_hash="a" * 64)
    frame = p.expand_values(prepared, first_ordinal=100, stop_ordinal=150)
    assert frame["boundary_at"][0] == p.completed_clock(scope, scope.first_bucket + 100)
    assert frame["available_at"].equals(frame["boundary_at"])
    assert frame["rolling_volume"].equals(prepared.clocks["rolling_volume"].slice(100, 50))
    unknown = pl.concat([prepared.values, prepared.values.head(1).with_columns(pl.lit(9999).cast(pl.UInt32).alias("field_id"))])
    with pytest.raises(ValueError, match="catalog"):
        p.expand_values(replace(prepared, values=unknown), first_ordinal=100, stop_ordinal=150)
    for clocks in (prepared.clocks.slice(1), prepared.clocks.with_columns(
            pl.when(pl.col("bucket_index") == scope.first_bucket).then(2)
            .otherwise(pl.col("source_known")).alias("source_known"))):
        with pytest.raises(ValueError, match="clock"):
            p.expand_values(replace(prepared, clocks=clocks), first_ordinal=100, stop_ordinal=150)


def test_producer_output_feeds_shared_vectorized_rule_evaluator_without_rebuilding_features():
    scope, bars, liquidity, fields = inputs()
    prepared = p.prepare_product(scope, bars, liquidity, fields, volume_window_seconds=30,
                                 producer_code_hash="a" * 64)
    frame = p.expand_values(prepared, first_ordinal=0, stop_ordinal=scope.clock_count)
    name = "history.attention_mean.58"
    contracts = {name: InputField(name, "dollars", "arte.qmd_causal_history_value_v1", scope.product_revision, 1000)}
    rule = RuleProgram((RuleNode(Operation.FEATURE, field=name),
        RuleNode(Operation.CONSTANT, literal=20., unit="dollars"),
        RuleNode(Operation.GREATER, left=0, right=1)), 2)
    result = evaluate_rules(frame, {"entry": rule}, contracts)
    expected = frame.select((pl.col(name + "__valid") & (pl.col(name) > 20.)).fill_null(False))
    assert result["entry"].to_list() == expected.to_series().to_list()
    assert result["entry__valid"].equals(frame[name + "__valid"].rename("entry__valid"))
    assert result["boundary_at"].equals(frame["boundary_at"])

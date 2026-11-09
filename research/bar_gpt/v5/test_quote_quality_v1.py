"""Checks for paired quarantine, condition namespaces and immutable quote repair."""

import io
from types import SimpleNamespace

import numpy as np
import pytest

from .quote_quality_v1 import condition_sql, neighbor_spikes, sanitize
from .quote_repair_v1 import phase_one_payload, phase_two_payload, publish_file
from .quote_rebuild_v1 import QUOTE
from .shards.phase1_contract import SAMPLE_DTYPE
from .phase2_shards.contract import BAR


def good(shape=(64,)):
    """Positive regular two-sided bars, with realistic counts and display sizes."""
    values = np.full((*shape, 18), 10, dtype=np.float32)
    values[..., (4, 10, 16)] = 100
    return values, np.ones_like(values, dtype=np.uint8)


@pytest.mark.parametrize(
    "column,value,reason",
    [
        (13, 170000, "extreme_trade_distance"),
        (10, 0, "nonpositive_size"),
        (6, -1, "nonpositive_price"),
        (9, 11, "ohlc_geometry"),
        (17, 1.5, "invalid_count"),
        (12, float("inf"), "nonfinite"),
    ],
)
def test_quarantine_preserves_trades_and_is_idempotent(column, value, reason):
    values, valid = good()
    values[30, column] = value
    clean, masks, counts = sanitize(values, valid)
    assert counts[reason] == 1 and counts["quarantined"] == 1
    assert not clean[30, 6:].any() and not masks[30, 6:].any()
    np.testing.assert_array_equal(clean[:, :6], values[:, :6])
    assert sanitize(clean, masks)[2]["quarantined"] == 0
    assert values[30, column] == value


def test_masked_quote_and_split_invariance():
    values, valid = good()
    values[30, 6:] = 0
    valid[30, 6:] = 0
    assert sanitize(values, valid)[2]["quarantined"] == 0
    values[20, 13] = 100000
    scaled = values.copy()
    scaled[..., (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)] *= 1000
    scaled[..., (4, 10, 16)] /= 1000
    np.testing.assert_array_equal(
        sanitize(values, valid)[1], sanitize(scaled, valid)[1]
    )


def test_condition_namespace_keeps_valid_corrections():
    rules = [
        dict(source_family="quote_conditions", token_id=48, modifier_int=81),
        dict(source_family="quote_conditions", token_id=22, modifier_int=20),
        dict(source_family="trade_conditions", token_id=81, modifier_int=81),
    ]
    expression = condition_sql(rules)
    assert "has([22, 48],t)" in expression
    assert "hasAny(tokens,[22])" in expression
    with pytest.raises(ValueError):
        condition_sql([])


def test_neighbor_spike_is_diagnostic_and_not_a_future_filter():
    values, valid = good()
    # An isolated 11x quote is suspicious temporally but inside the deliberately
    # conservative 100x causal gate. Neighbor evidence alone must not alter it.
    values[30, 12:16] = 110
    assert neighbor_spikes(values, valid).sum() == 1
    assert sanitize(values, valid)[2]["quarantined"] == 0


class FakeRebuilder:
    """Deterministic canonical replacement, independent of the old corrupted high."""

    actions = []

    def get(self, ticker, frame, ends):
        result = np.zeros(len(ends), dtype=QUOTE)
        result["rebuild"] = 1
        values, valid = good((len(ends),))
        result["values"], result["valid"] = values[:, 6:], valid[:, 6:]
        return result


def test_phase_one_rebuild_restores_high_instead_of_copying_close(tmp_path):
    data = np.zeros(1, dtype=SAMPLE_DTYPE)
    data["values"], data["valid"] = good((1, 64))
    data["values"][0, 30, 13] = 170000
    data["end_us"] = 1700000000000000 + np.arange(64) * 1000000
    data["timeframe_us"] = 1000000
    path = tmp_path / "old.npy"
    np.save(path, data)
    payload, counts = phase_one_payload(path, "AAPX", FakeRebuilder())
    revised = np.load(io.BytesIO(payload), allow_pickle=False)
    assert revised["values"][0, 30, 13] == 10 and counts["changed_bars"] == 1
    for field in ("end_us", "timeframe_us", "split"):
        np.testing.assert_array_equal(data[field], revised[field])
    np.testing.assert_array_equal(data["values"][..., :6], revised["values"][..., :6])
    assert np.load(path)["values"][0, 30, 13] == 170000


def test_packed_repair_preserves_all_non_quote_bytes(tmp_path):
    data = np.zeros(3, dtype=BAR)
    data["values"], data["valid"] = good((3,))
    data["end_us"] = 1700000000000000 + np.arange(3) * 1000000
    data["values"][1, 13] = 170000
    original = b"a" * 64 + data.tobytes() + b"z" * 64
    path = tmp_path / "payload.bin"
    path.write_bytes(original)
    descriptor = dict(kind="bar", offset=64, shape=[3], frame=0)
    payload, counts = phase_two_payload(path, [descriptor], "AAPX", FakeRebuilder())
    assert payload[:64] == b"a" * 64 and payload[-64:] == b"z" * 64
    actual = np.frombuffer(payload, dtype=BAR, count=3, offset=64)
    np.testing.assert_array_equal(actual["values"][:, :6], data["values"][:, :6])
    np.testing.assert_array_equal(actual["end_us"], data["end_us"])
    assert counts["changed_bars"] == 1 and path.read_bytes() == original

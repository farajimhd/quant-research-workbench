"""Verify pure-Torch UTC features against independent standard-library dates."""

from datetime import date, datetime, timedelta, timezone
import math

import pytest
import torch

from research.bar_gpt.v5.transforms_v1 import BarInputTransformV1, DAY_US, FEATURE_NAMES
from research.bar_gpt.v5.shards.contract import FRAMES


def clocks(start="2024-03-11T10:00:00", frame=60_000_000):
    """Build int64 UTC ends [1,64] without a calendar or dataframe dependency."""
    origin = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = origin - epoch
    start_us = delta.days * DAY_US + delta.seconds * 1_000_000 + delta.microseconds
    return (start_us + torch.arange(64, dtype=torch.int64) * frame)[None, :]


def apply(ends, frame=60_000_000):
    """Create neutral market tensors and transform them on the timestamp device."""
    values = torch.ones((*ends.shape, 21), device=ends.device)
    return BarInputTransformV1().to(ends.device)(values, torch.ones_like(values, dtype=torch.bool), ends, frame)


def feature(result, name):
    """Read one named channel without hardcoding the output column order."""
    return result.values[..., FEATURE_NAMES.index(name)]


def test_market_preservation_and_missing_evidence():
    """Market values stay unscaled; unavailable inputs and the first gap remain masked."""
    values = torch.arange(64*21, dtype=torch.float32).reshape(1, 64, 21)
    valid = torch.ones_like(values, dtype=torch.bool)
    values[0, 0, 0], valid[0, 0, 0] = float("nan"), False
    original = values.clone()
    result = BarInputTransformV1()(values, valid, clocks(), 60_000_000)
    assert result.values.shape == result.valid.shape == (1, 64, 30)
    assert result.values.dtype == torch.float32 and result.valid.dtype == torch.bool
    assert not {"premarket", "regular_hours", "after_hours"}.intersection(FEATURE_NAMES)
    torch.testing.assert_close(result.values[..., :21], torch.where(valid, values, 0))
    torch.testing.assert_close(values, original, equal_nan=True)
    torch.testing.assert_close(result.valid[..., :21], valid)
    assert not result.valid[0, 0, FEATURE_NAMES.index("log_gap_ratio")]
    assert result.valid[..., 21:].sum() == 64*9 - 1
    assert feature(result, "log_gap_ratio")[0, 0] == 0
    torch.testing.assert_close(feature(result, "bar_index")[0, [0, -1]], torch.tensor([0., 1.]))


@pytest.mark.parametrize("frame", FRAMES)
def test_all_timeframes_and_subsecond_precision(frame):
    """Independent UTC clock arithmetic verifies all six durations and adjacent gaps."""
    result = apply(clocks(frame=frame), frame)
    seconds = (10*3600 + torch.arange(64, dtype=torch.float64) * frame/1e6) % 86400
    torch.testing.assert_close(feature(result, "intraday_sin")[0], torch.sin(2*math.pi*seconds/86400).float())
    torch.testing.assert_close(feature(result, "intraday_cos")[0], torch.cos(2*math.pi*seconds/86400).float())
    torch.testing.assert_close(feature(result, "log_timeframe_seconds"), torch.full((1, 64), math.log1p(frame/1e6)))
    torch.testing.assert_close(feature(result, "log_gap_ratio")[0, 1:], torch.full((63,), math.log(2)))


def test_gregorian_cycle_against_standard_library():
    """Every day of a 400-year cycle checks leap centuries and ISO year boundaries."""
    first = date(1970, 1, 1)
    dates = [first + timedelta(days=i) for i in range(146097)]
    weekday, week, length = BarInputTransformV1._utc_week_fields(torch.arange(len(dates)))
    iso = [day.isocalendar() for day in dates]
    torch.testing.assert_close(weekday, torch.tensor([day.weekday() for day in dates]))
    torch.testing.assert_close(week, torch.tensor([item.week for item in iso]))
    torch.testing.assert_close(length, torch.tensor([date(item.year, 12, 28).isocalendar().week for item in iso]))


@pytest.mark.parametrize("start", [
    "2020-12-31T23:59:30", "2021-01-03T23:59:30", "2024-02-29T23:59:30",
    "2024-03-10T06:59:30", "2024-11-03T05:59:30",
])
def test_utc_midnight_iso_year_and_dst_dates(start):
    """UTC features follow actual end dates, including midnight and US DST dates."""
    result = apply(clocks(start, 1_000_000), 1_000_000)
    origin = datetime.fromisoformat(start)
    dates = [origin + timedelta(seconds=i) for i in range(64)]
    weekday_phase = torch.tensor([2*math.pi*d.weekday()/7 for d in dates], dtype=torch.float64)
    week_phase = torch.tensor([
        2*math.pi*(d.isocalendar().week-1)/date(d.isocalendar().year, 12, 28).isocalendar().week
        for d in dates
    ], dtype=torch.float64)
    for name, expected in (
        ("weekday_sin", weekday_phase.sin()), ("weekday_cos", weekday_phase.cos()),
        ("iso_week_sin", week_phase.sin()), ("iso_week_cos", week_phase.cos()),
    ):
        torch.testing.assert_close(feature(result, name)[0], expected.float())


def test_no_session_restrictions_and_mixed_frames():
    """Holidays and arbitrary UTC endpoints need no exchange schedule or session grid."""
    minute = clocks("2024-07-04T03:00:01")
    daily = clocks("2024-01-01T00:00:00", DAY_US)
    result = apply(torch.cat((minute, daily)), torch.tensor([60_000_000, DAY_US]))
    torch.testing.assert_close(result.values[0], apply(minute).values[0])
    torch.testing.assert_close(result.values[1], apply(daily, DAY_US).values[0])
    irregular = minute.clone()
    irregular[:, 32:] += 3*DAY_US  # Actual elapsed gaps remain observable across missing dates.
    gap = feature(apply(irregular), "log_gap_ratio")[0, 32]
    torch.testing.assert_close(gap, torch.tensor(math.log1p((3*DAY_US+60_000_000)/60_000_000)))


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda", marks=pytest.mark.skipif(
    not torch.cuda.is_available(), reason="CUDA hardware unavailable",
))])
def test_device_parity_and_gradients(device):
    """UTC arithmetic, evidence masks, and market gradients stay on the requested device."""
    transform = BarInputTransformV1().to(device)
    ends = clocks("2020-12-31T23:59:30", 1_000_000)
    values = torch.ones((1, 64, 21), device=device, requires_grad=True)
    valid = torch.ones_like(values, dtype=torch.bool)
    valid[0, 0, 0] = False
    actual = transform(values, valid, ends.to(device), 1_000_000)
    expected = BarInputTransformV1()(values.detach().cpu(), valid.cpu(), ends, 1_000_000)
    assert actual.values.device.type == actual.valid.device.type == device
    torch.testing.assert_close(actual.values.cpu(), expected.values)
    torch.testing.assert_close(actual.valid.cpu(), expected.valid)
    actual.values.sum().backward()
    torch.testing.assert_close(values.grad, valid.float())


def test_invalid_evidence_clocks_and_timeframes():
    """Removing session validation must preserve data-integrity checks."""
    transform = BarInputTransformV1()
    ends = clocks()
    values = torch.ones((1, 64, 21))
    valid = torch.ones_like(values, dtype=torch.uint8)
    values[0, 0, 0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        transform(values, valid, ends, 60_000_000)
    values[0, 0, 0], valid[0, 0, 0] = 1, 2
    with pytest.raises(ValueError, match="evidence"):
        transform(values, valid, ends, 60_000_000)
    valid[0, 0, 0] = 1
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        transform(values, valid, ends, 5_000_000)
    with pytest.raises(ValueError, match="int64 timestamps"):
        transform(values, valid, ends.float(), 60_000_000)
    ends[0, 1] = ends[0, 0]
    with pytest.raises(ValueError, match="increase"):
        transform(values, valid, ends, 60_000_000)

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from scripts.strategy_222_macd_episodes import analyze_frames, select_observed, closed_connection


def frame(at, histogram, close=10., high=None):
    return (at, dict(open=close, close=close, low=close, high=high or close,
                     bar_end=datetime.fromtimestamp(at, timezone.utc).isoformat()),
            dict(macd_line=histogram, macd_signal=0., macd_histogram=histogram))


@pytest.mark.parametrize('seconds', [1, 2, 5])
def test_future_does_not_change_prefix_features(seconds):
    rows = [frame(seconds, -.1), frame(seconds * 2, .1), frame(seconds * 3, .2),
            frame(seconds * 4, -.1)]
    full, labels = analyze_frames(rows, seconds)
    for count in range(1, len(rows) + 1):
        prefix, _ = analyze_frames(rows[:count], seconds)
        assert prefix == full[:count]
    assert labels[0]['duration_seconds'] == 2 * seconds
    assert not labels[0]['left_censored'] and not labels[0]['right_censored']
    assert all('end' not in f and 'duration_seconds' not in f for f in full)
    changed = deepcopy(rows)
    changed[-1] = frame(seconds * 4, 99., close=1000.)
    assert analyze_frames(changed, seconds)[0][:-1] == full[:-1]


def test_gaps_are_elapsed_time_not_fabricated_candles():
    features, labels = analyze_frames([frame(1, -.1), frame(2, .1), frame(12, .2), frame(15, 0)], 1)
    assert features[2]['observed_candles'] == 2
    assert features[2]['episode_age_seconds'] == 10
    assert features[2]['max_gap_seconds'] == 9
    assert labels[0]['positive_candles'] == 2
    assert labels[0]['closing_gap_seconds'] == 2


def test_censoring_is_separate_from_causal_snapshots():
    features, labels = analyze_frames([frame(5, .1), frame(10, .2)], 5)
    assert labels[0]['left_censored'] and labels[0]['right_censored']
    assert labels[0]['duration_seconds'] is None
    assert 'right_censored' not in features[0]


def test_price_scaling_preserves_normalized_geometry():
    rows = [frame(1, -.1), frame(2, .2, high=11), frame(3, .1)]
    scaled = deepcopy(rows)
    for _, bar, indicator in scaled:
        for k in ('open', 'close', 'high', 'low'):
            bar[k] *= 10
        for k in indicator:
            indicator[k] *= 10
    a, _ = analyze_frames(rows, 1)
    b, _ = analyze_frames(scaled, 1)
    for x, y in zip(a, b):
        for k in ('histogram_bps', 'histogram_slope_bps_per_second', 'pullback_pct'):
            if x.get(k) is None:
                assert y.get(k) is None
            else:
                assert x[k] == pytest.approx(y[k])


def test_delivered_watermark_excludes_same_time_undelivered_frame():
    snapshots, _ = analyze_frames([frame(5, -.1), frame(10, .1)], 5)
    selected = select_observed(snapshots, [5, 10], 5, 10)
    assert selected['as_of'] == 5 and not selected['positive']
    assert selected['source_age_seconds'] == 5
    assert select_observed(snapshots, [5, 10], None, 10)['status'] == 'no_delivered_frame_watermark'
    with pytest.raises(ValueError, match='future'):
        select_observed(snapshots, [5, 10], 10, 9)
    with pytest.raises(ValueError, match='absent'):
        select_observed(snapshots, [5, 10], 9, 10)


@pytest.mark.parametrize('rows', [[frame(1, .1), frame(1, .2)], [frame(2, .1), frame(1, .2)]])
def test_rejects_duplicate_or_reversed_frames(rows):
    with pytest.raises(ValueError, match='unordered'):
        analyze_frames(rows, 1)


def test_invalid_indicator_and_ohlc_evidence_fail_closed():
    row = frame(1, .1)
    row[2]['macd_histogram'] = .2
    with pytest.raises(ValueError, match='disagrees'):
        analyze_frames([row], 1)
    row = frame(1, float('nan'))
    with pytest.raises(ValueError, match='Nonfinite'):
        analyze_frames([row], 1)
    row = frame(1, .1)
    row[1]['low'] = 11
    with pytest.raises(ValueError, match='OHLC'):
        analyze_frames([row], 1)


def test_active_wal_is_not_read_as_immutable(tmp_path):
    path = tmp_path / 'journal.sqlite3'
    path.write_bytes(b'')
    path.with_name(path.name + '-wal').write_bytes(b'active')
    with pytest.raises(ValueError, match='writer'):
        closed_connection(path)

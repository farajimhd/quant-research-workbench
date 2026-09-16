from copy import deepcopy

import pytest

from src.market_engine.swing_pivot_witness import capture, event_times, PivotWitnessStructure
from src.market_engine.swing_structure import SwingSettings, SwingStructure


def level():
    return dict(level_id=7, side='support', scale='local', price=3.88,
        pivot_at=1, confirmed_at=2, lower=3.87, upper=3.89)


def test_new_witness_preserves_anchor_and_prior_witness():
    anchor = level()
    frozen = deepcopy(anchor)
    first = capture(anchor, (3.87, 10, .04), 11)
    old = deepcopy(first)
    second = capture(anchor, (3.885, 15, .04), 16)
    assert anchor == frozen
    assert first == old
    assert first['confirmed_at'] == 11
    assert second['confirmed_at'] == 16
    assert second['level_id'] == anchor['level_id']


@pytest.mark.parametrize('reason', ['boundary_retest_confirmed', 'retest_contact', 'role_reversal'])
def test_touch_or_role_change_is_not_a_new_directional_pivot(reason):
    assert capture(level(), (3.87, 10, None), 11, reason) is None


@pytest.mark.parametrize('extreme,at', [((3.87, 11, .04), 11), ((3.87, 12, .04), 11),
    ((3.87, 10, 0), 11), ((float('nan'), 10, .04), 11), ((3.87, 10, .04), float('inf'))])
def test_noncausal_or_invalid_witness_is_rejected(extreme, at):
    with pytest.raises(ValueError):
        capture(level(), extreme, at)


def test_explicit_clock_conversion_preserves_raw_sequence_witness():
    witness = capture(level(), (3.87, 10, .04), 11)
    converted = event_times(witness, {10:1000., 11:1001.})
    assert converted['pivot_at'] == 1000.
    assert converted['confirmed_at'] == 1001.
    assert converted['clock'] == 'event_time'
    assert witness['clock'] == 'candle_sequence'
    assert witness['pivot_at'] == 10
    with pytest.raises(ValueError):
        event_times(converted, {1000.:2000., 1001.:2001.})


@pytest.mark.parametrize('times', [{}, {10:1000.}, {10:1001., 11:1000.}, {10:1000., 11:float('nan')}])
def test_clock_conversion_never_invents_missing_timestamps(times):
    with pytest.raises(ValueError):
        event_times(capture(level(), (3.87, 10, .04), 11), times)


def test_observer_keeps_latest_pivot_without_changing_base_engine_outputs():
    observer, base = PivotWitnessStructure(SwingSettings()), SwingStructure(SwingSettings())
    for engine in (observer, base):
        engine._found(engine.detectors[0], (10., 1, .1), 'support', 2)
        engine._found(engine.detectors[0], (10., 3, .1), 'support', 4)
    assert observer.active == base.active
    assert observer.segments == base.segments
    assert observer.counts == base.counts
    assert observer.latest_pivots[1]['pivot_at'] == 3
    assert observer.latest_pivots[1]['confirmed_at'] == 4
    assert observer.active[1]['confirmed_at'] == 2
    assert observer._capturing_pivot is False
    assert observer._pivot_level_id is None


def test_merged_pivot_clocks_survive_anchor_clock_pruning():
    observer = PivotWitnessStructure(SwingSettings())
    observer._found(observer.detectors[0], (10., 1, .1), 'support', 2)
    observer._found(observer.detectors[0], (10., 3, .1), 'support', 4)
    anchor = observer.active[1]
    referenced = {anchor['pivot_at'], anchor['confirmed_at']} | observer.referenced_clocks()
    close_times = {k:1000.+k for k in range(1, 8) if k in referenced}
    assert close_times == {1:1001., 2:1002., 3:1003., 4:1004.}
    evidence = observer.event_evidence(anchor, close_times)
    assert evidence['pivot_at'] == 1003.
    assert evidence['confirmed_at'] == 1004.
    evidence['price'] = 999.
    assert observer.latest_pivots[1]['price'] == 10.
    observer._level_removed(1)
    assert observer.referenced_clocks() == set()
    assert observer.event_evidence(anchor, close_times) is None


@pytest.mark.parametrize('change', [{'side':'resistance'}, {'scale':'major'}, {'confirmed_at':5}])
def test_event_evidence_cannot_cross_anchor_role_or_newer_confirmation(change):
    observer = PivotWitnessStructure(SwingSettings())
    observer._found(observer.detectors[0], (10., 1, .1), 'support', 2)
    altered = dict(observer.active[1], **change)
    assert observer.event_evidence(altered, {1:1001., 2:1002.}) is None


@pytest.mark.parametrize('compact', [False, True])
def test_witness_checkpoint_roundtrip_preserves_future_merges(monkeypatch, compact):
    # The native detector registry is intentionally unchanged until its pinned
    # replay finishes. Exercise both real codecs with the prospective type entry.
    import json
    from src.market_engine import structural_detector_checkpoint as checkpoints
    monkeypatch.setitem(checkpoints.TYPES, 'PivotWitnessStructure', PivotWitnessStructure)
    observer = PivotWitnessStructure(SwingSettings())
    observer._found(observer.detectors[0], (10., 1, .1), 'support', 2)
    encode = checkpoints.encode_json if compact else checkpoints.encode
    decode = checkpoints.decode_json if compact else checkpoints.decode
    restored = decode(json.loads(json.dumps(encode(observer), allow_nan=False)))
    for engine in (observer, restored):
        engine._found(engine.detectors[0], (10., 3, .1), 'support', 4)
    assert restored.__dict__ == observer.__dict__
    assert restored.event_evidence(restored.active[1], {3:1003., 4:1004.}) == observer.event_evidence(observer.active[1], {3:1003., 4:1004.})


@pytest.mark.parametrize('change', ['side', 'confirmation', 'expiry'])
def test_observer_drops_witness_when_anchor_role_changes_or_expires(change):
    observer = PivotWitnessStructure(SwingSettings())
    observer._found(observer.detectors[0], (10., 1, .1), 'support', 2)
    if change == 'expiry':
        observer._level_removed(1)
    else:
        if change == 'side': observer.active[1]['side'] = 'resistance'
        else: observer.active[1]['confirmed_at'] = 3
        observer._level_updated(observer.active[1])
    assert observer.latest_pivots == {}

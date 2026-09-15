import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from strategy_222_level_features import level_features


def fixture():
    provenance = dict(catalog_hash='pinned', checkpoint_hash='prior')
    revision = dict(authority='qmd-prepared-causal-seconds-v1', input_hash='exact', request_complete=True)
    snapshot = dict(ticker='X', as_of=100, session_date='2026-01-02', max_input_timestamp=100,
        book_version='causal-level-book-v7-mle-1', provenance=provenance,
        source_audit=dict(source='qmd-prepared-causal-seconds', consumed_through=100,
            input_hash='exact', source_revision=revision), unified_levels=[])
    summary = dict(session_date='2026-01-02', experimental_structure_fingerprint='pinned',
        data_authority=dict(sources={'v7:X:2026-01-02': dict(checkpoint=provenance),
            'v7:X:2026-01-02:prepared-source': dict(source_revision=revision)}),
        configuration_revision=dict(payload=dict(strategy=dict(parameters=dict(
            historical_hod=dict(v7_zone_enabled=True, v7_encounters_enabled=True))))))
    return snapshot, summary


def level(identity, side, lower, center, upper, **changes):
    return dict(unified_level_id=identity, side=side, lower=lower, price=center, upper=upper,
        role={1: 'support', -1: 'resistance', 0: 'transition'}[side],
        book_version='causal-level-book-v7-mle-1', lifecycle='active',
        fit=dict(status='estimated'), historical=True, observation_count=10,
        confirmed_at_ms=90000, oldest_member_confirmed_at_ms=1000, available_at_ms=90000,
        **changes)


def extract(snapshot, summary, **kwargs):
    return level_features(snapshot, symbol='X', at=100, price=10, atr_pct=2, summary=summary,
        settings=summary['configuration_revision']['payload']['strategy']['parameters']['historical_hod'], **kwargs)


def test_roles_bands_and_atr_room_use_exact_shared_projection():
    snapshot, summary = fixture()
    snapshot['unified_levels'] = [level('s', 1, 9.7, 9.8, 9.9),
        level('r', -1, 10.1, 10.2, 10.3), level('t', 0, 9.9, 10, 10.1)]
    f, evidence = extract(snapshot, summary)
    assert f['levels.support.below.upper_distance_atr'] == pytest.approx(-.5)
    assert f['levels.resistance.above.lower_distance_atr'] == pytest.approx(.5)
    assert f['levels.transition.containing.price_location_in_band'] == pytest.approx(.5)
    assert f['levels.resistance.above.count_within_1_atr'] == 1
    assert f['levels.support.below.confirmation_age_s'] == 10
    assert evidence['selected_ids']['levels.resistance.above'] == 'r'
    assert all(not {'symbol', 'ticker', 'id', 'label', 'at'}.intersection(k.split('.')) for k in f)


def test_missing_bands_are_missing_not_infinite_room_and_future_rows_do_not_enter():
    snapshot, summary = fixture()
    future = level('future', -1, 10.1, 10.2, 10.3)
    future['confirmed_at_ms'] = 101000
    inactive = level('inactive', -1, 10.1, 10.2, 10.3)
    inactive['lifecycle'] = 'broken'
    snapshot['unified_levels'] = [future, inactive]
    f, evidence = extract(snapshot, summary)
    assert f['levels.resistance.above.available'] == 0
    assert 'levels.resistance.above.lower_distance_pct' not in f
    assert evidence['raw_count'] == 2 and evidence['eligible_count'] == 0


@pytest.mark.parametrize('bad', ['source', 'revision', 'catalog', 'future', 'symbol', 'available', 'duplicate'])
def test_rejects_source_drift_future_information_and_duplicate_identity(bad):
    snapshot, summary = fixture()
    # Deliberately break object sharing with the frozen expected identity.
    snapshot = copy.deepcopy(snapshot)
    snapshot['unified_levels'] = [level('r', -1, 10.1, 10.2, 10.3)]
    if bad == 'source': snapshot['source_audit']['source'] = 'qmd-history'
    if bad == 'revision': snapshot['source_audit']['source_revision']['input_hash'] = 'changed'
    if bad == 'catalog': snapshot['provenance']['catalog_hash'] = 'changed'
    if bad == 'future': snapshot['max_input_timestamp'] = 101
    if bad == 'symbol': snapshot['ticker'] = 'Y'
    if bad == 'available': snapshot['unified_levels'][0]['available_at_ms'] = 101000
    if bad == 'duplicate': snapshot['unified_levels'] *= 2
    with pytest.raises(ValueError): extract(snapshot, summary)


def test_disabled_transition_strategy_contract_excludes_transition():
    snapshot, summary = fixture()
    summary['configuration_revision']['payload']['strategy']['parameters']['historical_hod']['v7_zone_enabled'] = False
    snapshot['unified_levels'] = [level('t', 0, 9.9, 10, 10.1)]
    f, evidence = extract(snapshot, summary)
    assert evidence['projected_count'] == 1 and evidence['eligible_count'] == 0
    assert f['levels.transition.count'] == 0


def test_missing_atr_does_not_fill_normalized_distances():
    snapshot, summary = fixture()
    snapshot['unified_levels'] = [level('r', -1, 10.1, 10.2, 10.3)]
    f, _ = level_features(snapshot, symbol='X', at=100, price=10, atr_pct=None, summary=summary,
        settings=summary['configuration_revision']['payload']['strategy']['parameters']['historical_hod'])
    assert f['levels.resistance.above.lower_distance_pct'] == pytest.approx(1)
    assert not any('atr' in k for k in f)

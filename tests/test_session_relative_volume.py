from dataclasses import replace
import json

import pytest

from src.trading_runtime.session_relative_volume import SessionVolumeTracker, confirm, BASELINE_CONTRACT
from tests.test_market_pressure import trade, quote
from tests.test_quote_geometry import at


def baseline():
    return dict(contract=BASELINE_CONTRACT, content_hash='frozen-test-baseline',
                session_start=at(0).isoformat(), profiles={'TEST': [None, 10., 20., 30.]})


def observe(tracker, stamp, size, eligible=True):
    tracker.observe(replace(trade(size=size), ts=at(stamp),
                            raw=dict(volume_eligible=eligible, price_eligible=False)))


def test_exact_completed_boundary_and_restart():
    tracker = SessionVolumeTracker(at(0))
    observe(tracker, .5, 20)
    observe(tracker, 1., 1000)
    result = tracker.snapshot(at(1), baseline(), 'TEST')
    assert result['volume'] == 20 and result['ratio'] == 2
    assert confirm(result, now=1, minimum_ratio=2)['passed']
    assert not confirm(result, now=1, minimum_ratio=2.01)['passed']
    restored = SessionVolumeTracker(at(0), json.loads(json.dumps(tracker.checkpoint())))
    for obj in (tracker, restored):
        obj.observe(replace(quote(), ts=at(2)))
    assert tracker.snapshot(at(2), baseline(), 'TEST') == restored.snapshot(at(2), baseline(), 'TEST')
    assert tracker.snapshot(at(2), baseline(), 'TEST')['ratio'] == 51
    assert not confirm(result, now=2, minimum_ratio=.1)['passed']


def test_missing_baseline_is_not_zero_or_infinite_relative_volume():
    tracker = SessionVolumeTracker(at(0))
    observe(tracker, 0, 50)
    assert tracker.snapshot(at(0), baseline(), 'TEST')['reason'] == 'baseline_nonpositive_or_missing'
    assert not tracker.snapshot(at(1), baseline(), 'OTHER')['ready']
    assert not tracker.snapshot(at(4), baseline(), 'TEST')['ready']


@pytest.mark.parametrize('bad', ['missing_flag', 'out_of_order', 'future'])
def test_invalid_history_fails_closed(bad):
    tracker = SessionVolumeTracker(at(0))
    observe(tracker, .9, 10)
    if bad == 'missing_flag':
        tracker.observe(replace(trade(), ts=at(.95)))
    elif bad == 'out_of_order':
        observe(tracker, .8, 10)
    else:
        observe(tracker, 2, 10)
    assert not tracker.snapshot(at(1), baseline(), 'TEST')['ready']


def test_volume_ineligible_excluded_and_wrong_session_rejected():
    tracker = SessionVolumeTracker(at(0))
    observe(tracker, .1, 100, False)
    observe(tracker, .2, 20)
    assert tracker.snapshot(at(1), baseline(), 'TEST')['ratio'] == 2
    with pytest.raises(ValueError, match='identity'):
        SessionVolumeTracker(at(1), tracker.checkpoint())


@pytest.mark.parametrize('ratio', [None, .99, 1., 3.])
def test_entry_gate_and_pending_acquisition(ratio):
    from tests.test_v7_setup import prepared
    from src.trading_runtime import strategy_engine as S
    from src.trading_runtime.session_relative_volume import CONTRACT
    host, assignment, obs = prepared()
    assignment.parameters['historical_hod']['setup_minimum_session_relative_volume'] = 1.
    o = obs(2, 10.02)
    now = o.observed_at.timestamp()
    evidence = dict(contract=CONTRACT, observed_at=now, effective_at=int(now),
                    ratio=ratio, ready=ratio is not None, baseline_hash='test')
    entered = host.evaluate(assignment, replace(o, market_pressure={'session_relative_volume': evidence}))
    passed = ratio is not None and ratio >= 1
    assert any(i.action == 'enter_long' for i in entered.evaluation.intents) == passed
    assert entered.evaluation.signals[0].metadata['session_relative_volume']['passed'] == passed
    if passed:
        pending = replace(assignment, state=entered.state, status=S.AssignmentStatus.ENTRY_PENDING)
        result = host.evaluate(pending, replace(o, market_pressure={}))
        assert any(i.action == 'cancel_entry' for i in result.evaluation.intents)
        partial = host.evaluate(pending, replace(o, market_pressure={}, position_quantity=10, average_price=10.02))
        assert any(i.action == 'cancel_entry' for i in partial.evaluation.intents)
        assert not any(i.action in ('enter_long', 'add_long') for i in partial.evaluation.intents)


@pytest.mark.parametrize('value', [True, -.1, float('nan'), float('inf'), '1'])
def test_invalid_threshold_rejected(value):
    from tests.test_v7_setup import prepared
    from src.trading_runtime.historical_hod import configure
    _, assignment, _ = prepared()
    assignment.parameters['historical_hod']['setup_minimum_session_relative_volume'] = value
    with pytest.raises(ValueError):
        configure(assignment.parameters)


def test_baseline_artifact_is_pinned_and_missing_resume_artifact_rejected(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from src.backend import session_relative_volume as B
    session = date(2026, 8, 21)
    dates = []
    day = session
    while len(dates) < 20:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            dates.insert(0, day.isoformat())
    value = dict(contract=BASELINE_CONTRACT, session_date=str(session), sessions=dates,
                 session_start='2026-08-21T08:00:00+00:00', boundary_seconds=1,
                 profiles={'TEST': [None]+[10.]*57600}, content_hash='', content_hash_contract=B.HASH_CONTRACT,
                 source_revision=dict(complete_for_history=True, request_complete=True,
                                      token='source-token', source_plan_hash='source-plan'))
    value['content_hash'] = B.baseline_content_hash(value)
    calls = []
    def fetch(*args, **kwargs):
        calls.append(args)
        return value
    monkeypatch.setattr(B, 'qmd_history_post_json', fetch)
    store = B.BaselineStore(tmp_path, session)
    prepared = store.get('TEST')
    assert prepared['content_hash'] == value['content_hash']
    assert list(prepared['profiles']['TEST']) == value['profiles']['TEST']
    from datetime import datetime
    start = datetime.fromisoformat(value['session_start'])
    tracker = SessionVolumeTracker(start)
    tracker.observe(replace(trade(size=20), ts=start+timedelta(milliseconds=500),
                            raw=dict(volume_eligible=True)))
    assert tracker.snapshot(start+timedelta(seconds=1), prepared, 'TEST') == tracker.snapshot(
        start+timedelta(seconds=1), value, 'TEST')
    restored = B.BaselineStore(tmp_path, session, store.identities)
    assert list(restored.get('TEST')['profiles']['TEST']) == value['profiles']['TEST'] and len(calls) == 1
    store.close()
    restored.close()
    path = tmp_path/'TEST.json'
    # An existing artifact must be verified even before any checkpoint pins it.
    changed = json.loads(json.dumps(value))
    changed['profiles']['TEST'][-1] = 11.
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='hash changed'):
        B.BaselineStore(tmp_path, session).get('TEST')
    changed = dict(value, content_hash='changed')
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='changed'):
        B.BaselineStore(tmp_path, session, store.identities).get('TEST')
    path.unlink()
    with pytest.raises(ValueError, match='missing'):
        B.BaselineStore(tmp_path, session, store.identities).get('TEST')


def test_mapped_baselines_do_not_evict_or_read_files_on_cached_lookup(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from pathlib import Path
    from src.backend import session_relative_volume as B
    session = date(2026, 8, 21)
    dates = []
    day = session
    while len(dates) < 20:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            dates.insert(0, day.isoformat())
    tickers = [f'T{i}' for i in range(20)]
    def fetch(_url, payload, **_):
        ticker = payload['tickers'][0]
        value = dict(contract=BASELINE_CONTRACT, session_date=str(session), sessions=dates,
                     session_start='2026-08-21T08:00:00+00:00', boundary_seconds=1,
                     profiles={ticker: [None]+[10.]*57600}, content_hash='', content_hash_contract=B.HASH_CONTRACT,
                     source_revision=dict(complete_for_history=True, request_complete=True,
                                          token='source-token', source_plan_hash='source-plan'))
        value['content_hash'] = B.baseline_content_hash(value)
        return value
    monkeypatch.setattr(B, 'qmd_history_post_json', fetch)
    store = B.BaselineStore(tmp_path, session, allowed_tickers=tickers)
    try:
        with pytest.raises(ValueError, match='not prepared'):
            store.cached('T0')
        with pytest.raises(ValueError, match='outside'):
            store.get('OTHER')
        for ticker in tickers:
            store.get(ticker)
        assert len(store.cache) == 20  # Beyond the old LRU16 eviction boundary.
        def forbidden(*_, **__):
            raise AssertionError('Unexpected hot-path file IO or JSON parsing')
        monkeypatch.setattr(Path, 'open', forbidden)
        monkeypatch.setattr(Path, 'read_bytes', forbidden)
        monkeypatch.setattr(B.json, 'loads', forbidden)
        for ticker in tickers:
            view = store.cached(ticker)['profiles'][ticker]
            assert not isinstance(view, list)
            assert len(view) == 57601 and view[0] is None and view[1] == 10. and view[-1] == 10.
            assert store.get(ticker) is store.cached(ticker)
            with pytest.raises(TypeError):
                view[1] = 99.
        held = view
    finally:
        store.close()
    assert not list(tmp_path.glob('*.f64'))
    with pytest.raises(ValueError, match='closed'):
        store.cached('T0')
    with pytest.raises(ValueError):
        held[1]


def test_typed_json_hash_matches_rust_golden_and_json_roundtrip():
    from src.backend.session_relative_volume import baseline_content_hash
    value = {'z': [None, True, False, 0, -7, 18446744073709551615, 1.0, -0.0,
                   0.1, 5e-324, 1.7976931348623157e308], '\u00e9': '\U0001d11e',
             'a': {'content_hash': 'nested', 'x': 1.2345678901234567}, 'content_hash': 'ignored'}
    golden = 'sha256:a5d7ec2b86adbf614c2d62c180e1d856f6c57b2abb001913be7d99a1f7cdfdf2'
    assert baseline_content_hash(value) == golden
    assert baseline_content_hash(json.loads(json.dumps(value))) == golden
    assert baseline_content_hash(dict(reversed(list(value.items())))) == golden
    value['z'][6] = 1  # Numeric equality must not erase JSON type distinctions.
    assert baseline_content_hash(value) != golden


@pytest.mark.parametrize('bad', ['future_session', 'missing_weekday', 'missing_source_token'])
def test_baseline_semantic_provenance_rejected(bad):
    from datetime import date, timedelta
    from src.backend import session_relative_volume as B
    session = date(2026, 8, 21)
    dates = []
    day = session
    while len(dates) < 20:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            dates.insert(0, day.isoformat())
    value = dict(contract=BASELINE_CONTRACT, session_date=str(session), sessions=dates,
                 session_start='2026-08-21T08:00:00+00:00', boundary_seconds=1,
                 profiles={'TEST': [None]+[10.]*57600}, content_hash='', content_hash_contract=B.HASH_CONTRACT,
                 source_revision=dict(complete_for_history=True, request_complete=True,
                                      token='source-token', source_plan_hash='source-plan'))
    if bad == 'future_session':
        value['sessions'][-1] = str(session)
    elif bad == 'missing_weekday':
        value['sessions'][0] = (date.fromisoformat(dates[0])-timedelta(days=1)).isoformat()
    else:
        value['source_revision'].pop('token')
    value['content_hash'] = B.baseline_content_hash(value)
    with pytest.raises(ValueError):
        B.validate_baseline(value, 'TEST', session)

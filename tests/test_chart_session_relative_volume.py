from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.backend import chart_session_relative_volume as rvol

START = datetime(2026, 8, 21, 8, tzinfo=timezone.utc)


def stamp(seconds):
    return (START + timedelta(seconds=seconds)).isoformat()


def fixtures():
    baseline = dict(session_date='2026-08-21', session_start=stamp(0),
        profiles={'TEST': [None] + [10.] * 120}, content_hash='verified',
        source_revision={'token': 'baseline'}, sessions=['prior sessions'])
    numerator = dict(contract='session-volume-profile-1', ticker='TEST',
        session_date='2026-08-21', session_start=stamp(0), as_of=stamp(120),
        boundary_seconds=1, profile=[i * 2. for i in range(121)],
        source_revision=dict(complete_for_history=True, request_complete=True,
                             token='current', source_plan_hash='plan'))
    payload = dict(history=[dict(bar_start=stamp(60), bar_end=stamp(120), volume=1)],
                   indicators=[dict(bar_start=stamp(60), existing=7)])
    return payload, baseline, numerator


def test_full_session_prefix_not_visible_candle_volume():
    payload, baseline, numerator = fixtures()
    result = rvol.project(payload, ticker='TEST', as_of=stamp(120), baseline=baseline, numerator=numerator)
    assert result['indicators'][0]['session_relative_volume'] == 24
    assert result['indicators'][0]['existing'] == 7
    assert 'session_relative_volume' not in payload['indicators'][0]


def test_partial_and_future_bars_never_receive_values():
    payload, baseline, numerator = fixtures()
    payload['history'] += [dict(bar_start=stamp(120), bar_end=stamp(180)),
                           dict(bar_start=stamp(0), bar_end=stamp(60), is_closed=False)]
    result = rvol.project(payload, ticker='TEST', as_of=stamp(125), baseline=baseline, numerator=numerator)
    assert len(result['indicators']) == 1


@pytest.mark.parametrize('denominator', [None, 0])
def test_missing_denominator_is_null(denominator):
    payload, baseline, numerator = fixtures()
    baseline['profiles']['TEST'][120] = denominator
    assert rvol.project(payload, ticker='TEST', as_of=stamp(120), baseline=baseline,
                        numerator=numerator)['indicators'][0]['session_relative_volume'] is None


@pytest.mark.parametrize('mutation', ['future', 'start', 'coverage', 'decreasing', 'ticker'])
def test_rejects_untrusted_numerator(mutation):
    payload, baseline, numerator = fixtures()
    if mutation == 'future': numerator['as_of'] = stamp(121)
    if mutation == 'start': numerator['session_start'] = stamp(1)
    if mutation == 'coverage': numerator['source_revision']['request_complete'] = False
    if mutation == 'decreasing': numerator['profile'][119] = 0
    if mutation == 'ticker': numerator['ticker'] = 'OTHER'
    with pytest.raises(ValueError):
        rvol.project(payload, ticker='TEST', as_of=stamp(120), baseline=baseline, numerator=numerator)


def test_attach_selected_ticker_pinned_artifact_and_exact_closed_end(tmp_path, monkeypatch):
    payload, baseline, numerator = fixtures()
    run = tmp_path/'run'/'session-relative-volume'
    run.mkdir(parents=True)
    (run/'TEST.json').write_text('pinned evidence')
    store = MagicMock()
    store.__enter__.return_value = store
    store.cached.return_value = baseline
    def create(directory, day, expected, **kwargs):
        from pathlib import Path
        assert (Path(directory)/'TEST.json').read_text() == 'pinned evidence'
        assert expected == {'TEST': 'hash'}
        assert str(day) == '2026-08-21'
        return store
    monkeypatch.setattr(rvol, 'BaselineStore', create)
    fetch = MagicMock(return_value=numerator)
    monkeypatch.setattr(rvol, 'qmd_history_post_json', fetch)
    result = rvol.attach(payload, ticker='TEST', as_of=stamp(125), runtime_root=tmp_path,
                         run_directory=run.parent, pinned_hash='hash')
    assert result['indicators'][0]['session_relative_volume'] == 24
    store.prepare_many.assert_called_once_with(['TEST'])
    assert fetch.call_args.args[1]['as_of'] == stamp(120)
    store.__exit__.assert_called_once()


def test_unavailable_is_null_and_empty_history_does_no_work(tmp_path, monkeypatch):
    fetch = MagicMock(side_effect=RuntimeError('unavailable authority'))
    monkeypatch.setattr(rvol, 'BaselineStore', fetch)
    assert rvol.attach({'history': []}, ticker='TEST', as_of=stamp(120), runtime_root=tmp_path) == {'history': []}
    fetch.assert_not_called()
    result = rvol.attach(fixtures()[0], ticker='TEST', as_of=stamp(120), runtime_root=tmp_path)
    assert result['indicators'][0]['session_relative_volume'] is None
    assert result['indicator_provenance']['session_relative_volume']['status'] == 'unavailable'


@pytest.mark.parametrize('saved_review', [False, True])
def test_route_selection_strips_native_field_and_clamps_run_cursor(monkeypatch, tmp_path, saved_review):
    from src.backend import app
    controller = SimpleNamespace(current_time=START+timedelta(seconds=120), run_dir=tmp_path,
        definition=SimpleNamespace(session_date=START.date()),
        _session_relative_volume_store=SimpleNamespace(identities={'TEST': 'pinned'}))
    if saved_review:
        del controller._session_relative_volume_store
        controller.session_relative_volume_artifacts = {'TEST': 'pinned'}
    monkeypatch.setattr(app.backtest_run_service, 'get', lambda _: controller)
    monkeypatch.setattr(app, 'backtest_runtime_root', lambda: tmp_path)
    monkeypatch.setattr(app._CANVAS_CHART_HISTORY_CACHE, 'get_or_load', lambda key, load: load())
    history = MagicMock(return_value=fixtures()[0])
    monkeypatch.setattr(app, '_canvas_live_chart_history', history)
    attach = MagicMock(return_value={'projected': True})
    monkeypatch.setattr(rvol, 'attach', attach)
    kwargs = dict(symbol='TEST', stage='bars', mode='backtest', run_id='run',
                  session_date='2026-08-21', as_of=stamp(180), row_limit=100)
    app.trading_canvas_live_chart_history(**kwargs)
    attach.assert_not_called()
    assert app.trading_canvas_live_chart_history(**kwargs, indicator_columns='bar_start,session_relative_volume') == {'projected': True}
    assert history.call_args.kwargs['indicator_columns'] == ['bar_start']
    assert history.call_args.kwargs['as_of'] == stamp(120)
    assert attach.call_args.kwargs['pinned_hash'] == 'pinned'
    assert attach.call_args.kwargs['as_of'] == stamp(120)


def test_deferred_frontend_indicators_preserve_rvol_availability_evidence():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]/'frontend/src/features/canvas/chartData.ts').read_text()
    # Every deferred response must merge evidence: structure/EMA responses do
    # not carry RVOL status, but a new RVOL response must replace its old status.
    updates = [line.strip() for line in source.splitlines()
               if 'indicatorProvenance:' in line and 'current.indicatorProvenance' in line]
    assert updates
    assert all('...current.indicatorProvenance, ...payload.indicator_provenance' in line for line in updates)
    assert 'indicatorProvenance: payload.indicator_provenance ?? current.indicatorProvenance' not in source

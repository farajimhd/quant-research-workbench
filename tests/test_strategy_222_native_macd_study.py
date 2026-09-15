from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts import strategy_222_native_macd_study as study


def native():
    warmup = dict(status='ready', ticker='TEST', timeframe='2s', session_start='2026-08-21T08:00:00Z',
                  required_bars=1, bars=[dict(bar_start='2026-08-21T07:59:58Z', close=10.)])
    bars = [dict(sym='TEST', timeframe='2s', is_closed=True, open=10., high=11., low=9., close=10.,
                 bar_start=f'2026-08-21T08:00:0{i}Z', bar_end=f'2026-08-21T08:00:0{i+2}Z') for i in (0,2,4)]
    payload = dict(ticker='TEST', timeframe='2s', has_more=False, bars=bars,
                   indicators=[dict(bar_start=b['bar_start'], macd_line=.2, macd_signal=.1, macd_histogram=.1) for b in bars],
                   cache=dict(source_revision=dict(complete_for_history=True, request_complete=True)),
                   indicator_provenance=dict(complete=True))
    return payload, warmup


@pytest.mark.parametrize('failure', ['truncated','future_seed','open_bar','wrong_interval','duplicate_indicator','incomplete'])
def test_rejects_incomplete_or_noncausal_native_evidence(failure):
    payload, warmup = native()
    if failure == 'truncated': payload['has_more'] = True
    if failure == 'future_seed': warmup['bars'][0]['bar_start'] = '2026-08-21T08:00:00Z'
    if failure == 'open_bar': payload['bars'][0]['is_closed'] = False
    if failure == 'wrong_interval': payload['bars'][0]['bar_end'] = '2026-08-21T08:00:01Z'
    if failure == 'duplicate_indicator': payload['indicators'][1] = payload['indicators'][0]
    if failure == 'incomplete': payload['cache']['source_revision']['request_complete'] = False
    with pytest.raises(ValueError):
        study.validate_native(payload, warmup, 'TEST', 2, study.timestamp('2026-08-21T08:00:00Z'), study.timestamp('2026-08-21T08:00:06Z'))


def test_runnable_study_preserves_causal_cutoff_and_verifies_resume(tmp_path, monkeypatch):
    payload, warmup = native(); calls = []
    monkeypatch.setattr(study, 'qmd_materialize_indicator_warmup', lambda **kwargs: deepcopy(warmup))
    def fetch(request):
        calls.append(request)
        return SimpleNamespace(payload=deepcopy(payload))
    monkeypatch.setattr(study, 'qmd_product_request', fetch)
    source = tmp_path/'input.json'
    source.write_text(json.dumps(dict(run_id='test', authorities={'TEST:1s': {}}, entries=[dict(
        symbol='TEST', entry='2026-08-21T08:00:04Z', decision_at=study.timestamp('2026-08-21T08:00:04Z'), outcome='win', net=1.)])))
    output = tmp_path/'study'
    args = ([source], output, '2026-08-21T08:00:00Z', '2026-08-21T08:00:06Z', 2)
    study.run(*args)
    result = json.loads((output/'000.json').read_text())
    assert result['entries'][0]['feature']['as_of'] == study.timestamp('2026-08-21T08:00:02Z')
    assert result['entries'][0]['source_age_seconds'] == 2
    study.run(*args)
    assert len(calls) == 1
    (output/'000.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        study.run(*args)

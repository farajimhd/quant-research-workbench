import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from strategy_222_enrich_levels import attach, save_batch
from test_strategy_222_level_features import fixture, level


def inputs():
    snapshot, summary = fixture()
    summary['run_id'] = 'run'
    snapshot['unified_levels'] = [level('r', -1, 10.1, 10.2, 10.3)]
    row = dict(id='run:1', sequence=1, at=100, symbol='X',
        features={'candle_1s.atr_pct': 2, 'future_peak': 999}, label='major')
    decision = dict(ticker='X', source_signal_ids=['qmd-derived:X:1s:1'], metadata=dict(reference_price=10))
    settings = summary['configuration_revision']['payload']['strategy']['parameters']['historical_hod']
    return row, decision, snapshot, summary, settings


def test_enrichment_uses_only_causal_price_and_atr_not_labels():
    row, decision, snapshot, summary, settings = inputs()
    result = attach(row, ('1970-01-01T00:01:40+00:00', json.dumps(decision)), snapshot, summary, settings)
    assert result['features']['levels.resistance.above.lower_distance_atr'] == pytest.approx(.5)
    assert 'label' not in result and 'future_peak' not in result['features']
    row['label'] = 'loss'; row['features']['future_peak'] = -100
    assert attach(row, ('1970-01-01T00:01:40+00:00', json.dumps(decision)), snapshot, summary, settings) == result


@pytest.mark.parametrize('bad', ['id', 'time', 'ticker', 'timeframe'])
def test_enrichment_fails_closed_on_decision_mismatch(bad):
    row, decision, snapshot, summary, settings = inputs()
    if bad == 'id': row['id'] = 'other:1'
    if bad == 'time': row['at'] = 99
    if bad == 'ticker': decision['ticker'] = 'Y'
    if bad == 'timeframe': decision['source_signal_ids'] = ['qmd-derived:X:5s:1']
    with pytest.raises(ValueError):
        attach(row, ('1970-01-01T00:01:40+00:00', json.dumps(decision)), snapshot, summary, settings)


def test_compressed_evidence_is_deterministic_and_rejects_nonfinite(tmp_path):
    a = tmp_path / 'a.json.gz'; b = tmp_path / 'b.json.gz'
    data = dict(rows=[dict(id='run:1', features={'distance': .5})], snapshots={'100': {'as_of': 100}})
    save_batch(a, data); save_batch(b, data)
    assert a.read_bytes() == b.read_bytes()
    assert json.loads(gzip.decompress(a.read_bytes())) == data
    assert not a.with_suffix('.tmp').exists()
    with pytest.raises(ValueError): save_batch(b, dict(value=float('nan')))
    assert b.read_bytes() == a.read_bytes()

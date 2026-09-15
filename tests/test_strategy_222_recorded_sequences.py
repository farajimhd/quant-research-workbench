import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_recorded_sequences import RecordedSequences, complete_sparse_labels, recording
from strategy_222_candle_sequences import CandleSequences
from strategy_222_feature_comparison import sample
from src.market_engine.structural_detector import StructuralDetector


def saved(tmp_path):
    detector = StructuralDetector()
    sequence = CandleSequences(candle_seconds=1)
    rows = []
    for i in range(8):
        row = detector.observe(dict(time=i, end=i+1, open=10., high=10.5, low=9.8, close=9.9))
        rows.append(dict(run_id='run', symbol='TEST', at=i+1, candle=row['candle'],
            **sequence.observe(row, session='session', observed_at=i+1)))
    path = tmp_path/'rows.jsonl.gz'
    with gzip.open(path, 'wt') as f:
        for row in rows:
            f.write(json.dumps(row)+'\n')
    receipt = dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        rows=len(rows), by_symbol={'TEST':len(rows)}, status='completed', timeframe='1s')
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps(dict(trials=[dict(run_id='run',status='completed',candle_sequences=receipt)])))
    return manifest, path, receipt


def test_verifies_hash_counts_and_requested_window(tmp_path):
    manifest, path, receipt = saved(tmp_path)
    assert recording(manifest,'run') == (path,receipt)
    reader = RecordedSequences(path,receipt,'run',{'TEST':[(3,6)]})
    assert len(reader.streams['TEST']) == 4
    assert reader.at('TEST',2)[1]['status'] == 'missing_fresh_native_sequence'
    features,evidence = reader.at('TEST',5.5)
    assert features['candles_5.red_upper_tail_run'] == 5
    assert evidence['through'] == 5
    assert evidence['source_age_seconds'] == .5
    assert reader.at('TEST',9)[1]['status'] == 'missing_fresh_native_sequence'
    with pytest.raises(ValueError,match='coverage'):
        RecordedSequences(path,dict(receipt,rows=9),'run',{})
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='hash'):
        recording(manifest,'run')


def test_sparse_absence_is_zero_only_for_complete_windows():
    key='candles_5.label_fraction.geometry:upper_tail'
    rows=[dict(features={key:1.},sequence_authority=dict(status='measured',complete_windows=[3,5])),
          dict(features={},sequence_authority=dict(status='measured',complete_windows=[3,5])),
          dict(features={},sequence_authority=dict(status='measured',complete_windows=[3])),
          dict(features={},sequence_authority=dict(status='missing_fresh_native_sequence'))]
    complete_sparse_labels(rows)
    assert rows[1]['features'][key] == 0
    assert key not in rows[2]['features'] and key not in rows[3]['features']


def test_sample_respects_pre_fill_decision_boundary(tmp_path):
    _,path,receipt = saved(tmp_path)
    reader=RecordedSequences(path,receipt,'run',{'TEST':[(0,8)]})
    decisions=[(5.,9,dict(action='enter_long',reason='entry',metadata={})),
               (5.,11,dict(action='wait',reason='after_fill',metadata={})),
               (6.,12,dict(action='wait',reason='future',metadata={}))]
    value=sample(dict(at=5.,sequence_limit=10,symbol='TEST'),0,decisions,sequence_states=reader)
    assert value['decision_sequence'] == 9
    assert value['sequence_authority']['through'] == 5.
    assert value['features']['candles_5.red_upper_tail_run'] == 5

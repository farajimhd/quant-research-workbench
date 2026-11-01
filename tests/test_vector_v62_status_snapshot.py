import json
from pathlib import Path
import pytest
from research.vectorized_backtest.v62.status_snapshot import StatusWriter,expand,_payload


def test_ranking_payload_written_once_and_full_api_restored(tmp_path,monkeypatch):
    import research.vectorized_backtest.v62.status_snapshot as mod
    calls=[]
    def write(path,raw):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(raw);calls.append(path)
    monkeypatch.setattr(mod,'_write_text',write)
    writer=StatusWriter(tmp_path);rows=[dict(serial=i,score=float(i)) for i in range(1024)]
    for generation in range(4):
        value=writer.compact(dict(training_ranking=rows,validation_ranking=[],generation=generation))
        assert len(json.dumps(value))<600
        assert expand(tmp_path/'full-status.json',value)['training_ranking']==rows
    assert len(calls)==2
    new=writer.compact(dict(training_ranking=[dict(serial=3,score=7)],generation=4))
    assert expand(tmp_path/'full-status.json',new)['training_ranking'][0]['score']==7
    assert len(calls)==3


def test_bad_status_reference_rejected(tmp_path):
    with pytest.raises(ValueError,match='path'):
        expand(tmp_path/'full-status.json',dict(status_payloads={'training_ranking':dict(path='../secret',sha256='a'*64)}))
    value=dict(generation=1,training_ranking=[])
    assert expand(tmp_path/'full-status.json',value) is value

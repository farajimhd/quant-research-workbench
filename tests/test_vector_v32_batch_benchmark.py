"""Benchmark refusal must not append failures into an existing campaign."""
import json
import sys
import pytest
from research.vectorized_backtest.v32 import benchmark_session_batches as benchmark


def test_existing_output_is_read_only_even_on_failure(tmp_path,monkeypatch):
    campaign=tmp_path/'campaign'
    campaign.mkdir()
    sentinel=campaign/'checkpoint.json'
    sentinel.write_bytes(b'preserved checkpoint')
    job=tmp_path/'job.json'
    job.write_text(json.dumps({'output':str(campaign)}))
    monkeypatch.setattr(sys,'argv',['benchmark','--job',str(job)])
    with pytest.raises(FileExistsError):
        benchmark.main()
    assert list(campaign.iterdir())==[sentinel]
    assert sentinel.read_bytes()==b'preserved checkpoint'

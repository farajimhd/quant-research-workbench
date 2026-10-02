"""Read-only teacher inspection must preserve overlaps and original label mass."""
import polars as pl
import pytest
import json
import numpy as np
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from research.rl_trading.v6 import label_audit as audit
from src.backend.research_model_service import router


def test_statistics_preserve_overlap_and_soft_mass():
    flat = pl.DataFrame(dict(listing_id=['a', 'a', 'a'], ticker=['XYZ']*3,
        episode_uid=['e1', 'e1', 'e2'], time_us=[1000000, 2000000, 1000000],
        enter_probability=[1., 0., .5], sample_weight=[.5, .5, 1.]))
    held = pl.DataFrame(dict(listing_id=['a'], ticker=['XYZ'], episode_uid=['e1'],
        time_us=[2000000], exit_probability=[1.], sample_weight=[1.]))
    result = audit.statistics(dict(flat=flat, held=held))
    classes = {r['action']: r for r in result['classes']}
    assert classes['ENTRY']['rows'] == 2
    assert classes['ENTRY']['weighted_soft_mass'] == 1.
    assert classes['WAIT']['rows'] == 1
    assert classes['WAIT']['weighted_soft_mass'] == 1.
    assert result['branches'][0]['overlapping_clocks'] == 1
    assert result['branches'][0]['extra_overlap_rows'] == 1
    assert result['tickers'][0]['rows'] == 4


def test_holdout_rejected_before_reading_runtime(monkeypatch):
    monkeypatch.setattr(audit, 'runtime', lambda: pytest.fail('Holdout touched filesystem'))
    with pytest.raises(ValueError, match='sealed'):
        audit.sources('2026-08-26')
    with pytest.raises(ValueError, match='selected RTH run'):
        audit.sources('2026-08-05')


def test_run_window_keeps_exact_probabilities_and_original_weights():
    begin, end = audit.run_window('2026-07-31')
    frame = pl.DataFrame(dict(time_us=[begin-1, begin, end-1, end],
                             probability=[.1, .2, .3, .4], sample_weight=[.01]*4))
    result = audit.training_frames('2026-07-31', dict(flat=frame, held=frame))
    assert result['flat']['time_us'].to_list() == [begin, end-1]
    assert result['flat']['probability'].to_list() == [.2, .3]
    assert result['held']['sample_weight'].to_list() == [.01, .01]


def test_routes_reject_invalid_requests_and_expose_no_writers():
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    catalog = client.get('/api/research/models').json()
    assert len(catalog['models'][0]['days']) == 5
    assert all(row['day'] != '2026-08-26' for row in catalog['models'][0]['days'])
    assert client.get('/api/research/models/v6/preflight?day=2026-08-26').status_code == 409
    assert client.get('/api/research/models/v6/chart?day=2026-07-31&listing_id=a&seconds=3601').status_code == 422
    assert client.get('/api/research/models/v6/chart?day=2026-07-31&listing_id=a&branch=oops').status_code == 422
    assert client.post('/api/research/models/v6/preflight').status_code == 405


def test_changed_label_bytes_fail_closed(tmp_path, monkeypatch):
    bank = tmp_path / 'bank'; bank.mkdir()
    labels = tmp_path / 'labels'; labels.mkdir()
    episodes = pl.DataFrame(dict(listing_id=['a'], ticker=['XYZ']))
    episodes.write_parquet(bank / 'episodes.parquet')
    episode_hash = audit.file_hash(bank / 'episodes.parquet')
    (bank / 'complete.json').write_text(json.dumps(dict(outputs=dict(episodes=dict(sha256=episode_hash)))))
    bank_hash = audit.file_hash(bank / 'complete.json')
    frame = pl.DataFrame(dict(listing_id=['a'], episode_uid=['e'], time_us=[1000000],
                             sample_weight=[1.], enter_probability=[1.]))
    frame.write_parquet(labels / 'flat.parquet')
    frame.rename({'enter_probability': 'exit_probability'}).write_parquet(labels / 'held.parquet')
    proof = dict(version=audit.VERSION, status='audited_independent_episode_windows',
        day='2026-07-31', role='train', sealed_test_accessed=False,
        bank_certificate_sha256=bank_hash, episodes_sha256=episode_hash,
        files={name: dict(sha256=audit.file_hash(labels / (name+'.parquet')), rows=1) for name in ['flat', 'held']})
    (labels / 'complete.json').write_text(json.dumps(proof))
    monkeypatch.setattr(audit, 'sources', lambda day: (dict(role='train', bank_certificate_sha256=bank_hash), bank, labels))
    audit._verified_labels.cache_clear()

    assert audit.labels('2026-07-31')[0]['flat'].height == 1
    frame.with_columns(pl.lit(0.).alias('enter_probability')).write_parquet(labels / 'flat.parquet')
    with pytest.raises(ValueError, match='hash mismatch'):
        audit.labels('2026-07-31')
    audit._verified_labels.cache_clear()


def test_chart_verifies_only_consumed_files_and_rejects_changed_scalar(tmp_path, monkeypatch):
    root = tmp_path / 'bank'; root.mkdir()
    np.save(root / 'close_us.npy', np.array([1_000_000], dtype=np.int64))
    np.save(root / 'scalar.npy', np.zeros((1, len(audit.SCALAR_NAMES)), dtype=np.float32))
    hashes = {name: audit.file_hash(root / name) for name in ['close_us.npy', 'scalar.npy']}
    hashes['levels.npy'] = 'unused-level-proof'
    manifest = dict(files_sha256=hashes, scalar_names=list(audit.SCALAR_NAMES), candle_count=1, offsets={'a': [0, 1]})
    (root / 'complete.json').write_text(json.dumps(manifest))
    (tmp_path / 'complete.json').write_text(json.dumps(dict(bank_file_hashes=hashes)))
    monkeypatch.setattr(audit, 'sources', lambda day: ({}, tmp_path, tmp_path))
    audit._verified_bank.cache_clear()
    # No levels file exists: it cannot be touched by candle/indicator inspection.
    assert audit._verified_bank('2026-07-31', ('first',))[1].tolist() == [1_000_000]
    audit._verified_bank.cache_clear()  # Release Windows mmap before changing bytes.
    np.save(root / 'scalar.npy', np.ones((1, len(audit.SCALAR_NAMES)), dtype=np.float32))
    with pytest.raises(ValueError, match='Chart input hash mismatch'):
        audit._verified_bank('2026-07-31', ('changed',))
    audit._verified_bank.cache_clear()

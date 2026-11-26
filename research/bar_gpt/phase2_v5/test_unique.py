from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from . import data
from .data import Session, SlowContextCache
from .model import HORIZONS
from .unique_data import UniqueRelease, merge_sessions, payload, read_session
from .migrate_unique import write_payload
from ..v5.phase2_ar_shards.contract import BAR


def example(offset, origins):
    pool = np.zeros(90, dtype=BAR)
    scale = 86_400_000_000
    pool["end_us"] = np.arange(offset, offset + len(pool)) * scale
    pool["values"] = 1
    pool["valid"] = 1
    times = np.asarray(origins)
    ends = np.repeat((times-offset)[:,None], 5, axis=1)
    targets = [ends[:, :1]+1+np.arange(h) for h in HORIZONS]
    return Session([pool.copy() for _ in HORIZONS], ends, targets, times * scale, "test", 0), {"basis":1}


def test_unique_round_trip(tmp_path):
    a, b = example(0, [70,71]), example(5, [71,72,73])
    b[0].targets[0][-1,:] = -1
    merged = merge_sessions([a,b])
    np.testing.assert_array_equal(merged.origins,np.array([70,71,72,73])*86_400_000_000)
    np.testing.assert_array_equal(merged.endpoints[:,0],[70,71,72,73])
    np.testing.assert_array_equal(merged.targets[0][-1],np.arange(74,84))
    path = Path(tmp_path)/"data.npz"
    sha = write_payload(path,payload(merged))
    loaded = read_session(path,sha,4,4)
    np.testing.assert_array_equal(loaded.pools[0],merged.pools[0])


def test_conflicting_context_or_target_fails():
    a,b = example(0,[70,71]),example(5,[71,72])
    b[0].pools[0]["values"][30,0] += 1
    with pytest.raises(ValueError,match="bar bytes disagree"):
        merge_sessions([a,b])
    b = example(5,[71,72])
    b[0].targets[0][0,0] += 1
    audit = []
    merge_sessions([a,b],audit=audit)
    assert audit[0]["shifted"] == 1


def test_local_order_is_deterministic_and_keeps_every_session():
    rows = pl.DataFrame({"path": [f"ticker-{i // 10}/{i % 10:02d}" for i in range(60)],
                         "split": [0] * 50 + [1] * 10, "candidates": range(60)})
    release = UniqueRelease(Path("."), rows, locality=8)
    first = release.order_for_pass(0)
    second = release.order_for_pass(1)
    assert first.rows.equals(release.order_for_pass(0).rows)
    assert not first.rows.equals(second.rows)
    assert first.rows.sort("path").equals(rows)
    assert second.rows.sort("path").equals(rows)
    assert first.rows.tail(10).equals(rows.tail(10))
    assert first.candidate_prefix(len(first), 0) == sum(range(50))


def test_slow_cache_binds_frame_clock_values_and_validity(monkeypatch):
    calls = []

    def encode(_teacher, features, rows):
        calls.append(len(rows))
        return features[rows].sum(1)

    monkeypatch.setattr(data, "encode_cached", encode)
    pool = example(0, [70])[0].pools[0]
    gather = torch.arange(7, 71)[None]
    features = torch.arange(len(pool))[:, None]
    cache = SlowContextCache()
    original, hits = cache.encode(None, 3, pool, [70], features, gather)
    assert hits == 0
    same, hits = cache.encode(None, 3, pool.copy(), [70], features, gather)
    torch.testing.assert_close(same, original)
    assert hits == 1
    assert cache.encode(None, 4, pool, [70], features, gather)[1] == 0
    pool["valid"][50, 0] = 0
    assert cache.encode(None, 3, pool, [70], features, gather)[1] == 0
    assert calls == [1, 1, 1]

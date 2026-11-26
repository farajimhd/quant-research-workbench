from pathlib import Path

import numpy as np
import pytest

from .data import Session
from .model import HORIZONS
from .unique_data import merge_sessions, payload, read_session
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

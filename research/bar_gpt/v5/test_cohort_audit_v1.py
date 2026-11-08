"""Completion audit rejects missing features and inconsistent final inventories."""

from collections import Counter
import json

import numpy as np
import pytest

from research.bar_gpt.v5 import cohort_audit_v1 as audit_module
from research.bar_gpt.v5.shards.phase1_contract import (
    FRAMES,
    SAMPLE_DTYPE,
    digest,
    write_json,
)
from research.bar_gpt.v5.shards.storage import BAR_DTYPE, Budget, publish


def test_audit_checks_both_members_and_rejects_counter_mismatch(tmp_path, monkeypatch):
    roots = [(name, tmp_path / name) for name in ("base", "extra")]
    for name, root in roots:
        root.mkdir()
        body = dict(version="fixture", configuration=dict(target_bytes=100000))
        binding = digest(body)
        write_json(root / "contract.json", dict(contract=body, binding=binding))
        frames = Counter()
        for frame in FRAMES:
            data = np.zeros(2, dtype=SAMPLE_DTYPE)
            data["values"], data["valid"], data["timeframe_us"] = 10, 1, frame
            data["end_us"] = 1600000000000000 + np.arange(64)[None, :] * frame
            publish(
                root / "jobs/ticker" / str(frame),
                data,
                np.empty(0, dtype=BAR_DTYPE),
                dict(binding=binding, identity=str(frame), day="2020-01-02"),
                Budget(10**8),
                32 * 1024**2,
            )
            frames[str(frame)] = 2
        completion = dict(samples=12, frames=frames, bytes=100000)
        if name != "base":
            completion["target_bytes"] = 100000
        write_json(root / "result.json", completion)
    monkeypatch.setattr(audit_module, "members", lambda *a, **kw: ("fixture", roots))
    result = audit_module.audit(roots[0][1], tmp_path / "audit.json")
    assert result["state"] == "passed" and result["samples"] == 24
    assert all(len(member["verified_payloads"]) == 6 for member in result["members"])
    path = roots[1][1] / "result.json"
    result = json.loads(path.read_text())
    result["samples"] += 1
    write_json(path, result)
    with pytest.raises(ValueError, match="reconcile"):
        audit_module.audit(roots[0][1], tmp_path / "bad.json")

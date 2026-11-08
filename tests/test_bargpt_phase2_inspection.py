"""Exercise real sealed mixed-format units, causal origins and split-day UI data."""

import json

import numpy as np
import pytest

from research.bar_gpt.v5.phase2_shards import inspection
from research.bar_gpt.v5.phase2_shards.contract import (
    BAR,
    FRAMES,
    SPECS,
    VERSION,
    Context,
    digest,
)
from research.bar_gpt.v5.phase2_shards.storage import publish
from research.bar_gpt.v5.shards.sql import clock


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    """Seed historical raw bars, then publish a packed split-day decision block."""
    key = "a" * 24
    stream = dict(key=key, ticker="EXAMPLE", year=2025, liquid=True)
    plan = dict(
        version=VERSION,
        streams=[stream],
        actions=dict(
            rows=[
                dict(
                    provider_ticker="EXAMPLE",
                    execution_date="2025-06-03",
                    split_from=1,
                    split_to=2,
                )
            ]
        ),
    )
    plan["hash"] = digest(plan)
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "progress.json").write_text(
        json.dumps(dict(state="running", bytes=1000, units=2, updated=1))
    )
    monkeypatch.setattr(inspection, "ROOT", tmp_path)
    context = Context()
    historical = []
    for duration, width, _ in SPECS:
        rows = np.zeros(width, dtype=BAR)
        rows["values"], rows["valid"] = 100, 1
        rows["end_us"] = (
            clock("2025-06-02", 16) - np.arange(width - 1, -1, -1) * duration
        )
        historical.append(rows)
    starts = context.counts.copy()
    blocks, splits = context.append(historical, decisions=False)
    publish(
        tmp_path,
        stream,
        0,
        historical,
        starts,
        blocks,
        splits,
        context,
        dict(plan=plan["hash"], begin="2025-01-01", end="2025-06-02"),
    )
    current = [np.empty(0, dtype=BAR) for _ in FRAMES]
    current[0] = np.zeros(256, dtype=BAR)
    current[0]["values"], current[0]["valid"] = 50, 1
    current[0]["end_us"] = clock("2025-06-03", 10) + np.arange(1, 257) * FRAMES[0]
    starts = context.counts.copy()
    blocks, splits = context.append(current)
    receipt = publish(
        tmp_path,
        stream,
        1,
        current,
        starts,
        blocks,
        splits,
        context,
        dict(plan=plan["hash"], begin="2025-06-03", end="2025-06-03"),
        storage_format="packed-v2",
    )
    return key, receipt


def test_inspector_reads_mixed_units_and_rebases_daily_to_exact_decision_close(dataset):
    key, _ = dataset
    result = inspection.sample(key, unit=1, frame=4)
    assert result["origin_us"] == clock("2025-06-03", 10) + 256 * FRAMES[0]
    assert len(result["bars"]) == 64 and len(result["time_channels"]) == 9
    assert (
        sum(
            sum(view["available"] for view in row["views"])
            for row in result["summaries"]
        )
        == 38
    )
    assert result["bars"][-1]["end_us"] < result["origin_us"]
    assert result["bars"][-1]["raw"][0] == 100
    assert result["bars"][-1]["values"][0] == 50
    assert result["bars"][-1]["values"][4] == 200
    assert result["bars"][-1]["values"][5] == 100
    assert result["actions"][0]["effective_by_origin"]
    assert inspection.catalog("EXA", 2025)["total"] == 1
    assert [row["unit"] for row in inspection.units(key)["units"]] == [1]


def test_inspector_fails_closed_on_payload_mutation_and_paths(dataset):
    key, receipt = dataset
    inspection.sample(key, unit=1)  # Warm the hash cache before changing a sealed file.
    with pytest.raises(ValueError, match="Invalid"):
        inspection.units("../outside")
    path = inspection.ROOT / "streams" / key / "0001" / receipt["files"][0]["name"]
    with path.open("r+b") as handle:
        handle.seek(-1, 2)
        handle.write(b"\xff")
    with pytest.raises(ValueError, match="SHA-256"):
        inspection.sample(key, unit=1)


def test_inspector_rejects_receipt_rebinding(dataset):
    key, _ = dataset
    path = inspection.ROOT / "streams" / key / "0001" / "receipt.json"
    receipt = json.loads(path.read_text())
    receipt["ticker"] = "OTHER"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="receipt hash"):
        inspection.units(key)


def test_early_decision_with_incomplete_history_does_not_invent_bars(
    tmp_path, monkeypatch
):
    key = "b" * 24
    stream = dict(key=key, ticker="EARLY", year=2025, liquid=False)
    plan = dict(version=VERSION, streams=[stream], actions=dict(rows=[]))
    plan["hash"] = digest(plan)
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(inspection, "ROOT", tmp_path)
    arrays = [np.empty(0, dtype=BAR) for _ in FRAMES]
    arrays[0] = np.zeros(256, dtype=BAR)
    arrays[0]["values"], arrays[0]["valid"] = 1, 1
    arrays[0]["end_us"] = clock("2025-06-03", 10) + np.arange(1, 257) * FRAMES[0]
    context = Context()
    blocks, splits = context.append(arrays)
    publish(
        tmp_path,
        stream,
        0,
        arrays,
        [0] * 5,
        blocks,
        splits,
        context,
        dict(plan=plan["hash"], begin="2025-06-03", end="2025-06-03"),
    )
    first = inspection.sample(key, unit=0, decision=0)
    assert first["origin_us"] == arrays[0]["end_us"][0]
    assert first["bars"] == [] and first["view"] is None
    ready = inspection.sample(key, unit=0, decision=63)
    assert len(ready["bars"]) == 64 and ready["view"] == 15

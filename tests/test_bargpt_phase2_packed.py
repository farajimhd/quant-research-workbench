"""Lossless mixed-format reads, bounded mmap payloads and context corruption checks."""

import numpy as np
import pytest

from research.bar_gpt.v5.phase2_shards.contract import BAR, Context, CUTOFF, FRAMES
from research.bar_gpt.v5.phase2_shards.packed_storage_v2 import Writer, LIMIT, load
from research.bar_gpt.v5.phase2_shards.storage import (
    publish,
    restore,
    load_array,
    block_segments,
    read_window,
)


def test_mixed_legacy_and_packed_units_keep_every_array_and_context(tmp_path):
    """One 64-bar view crosses a physical format transition without changing values."""
    stream = dict(key="mixed", ticker="EXAMPLE", year=2025, liquid=1)
    context = Context()
    for unit, format_name in enumerate(("legacy", "packed-v2")):
        arrays = []
        for frame in FRAMES:
            rows = np.zeros(320, dtype=BAR)
            rows["values"], rows["valid"] = 1, 1
            rows["end_us"] = CUTOFF + frame * np.arange(
                unit * 320 + 1, (unit + 1) * 320 + 1
            )
            arrays.append(rows)
        starts = context.counts.copy()
        blocks, splits = context.append(arrays)
        receipt = publish(
            tmp_path,
            stream,
            unit,
            arrays,
            starts,
            blocks,
            splits,
            context,
            {},
            storage_format=format_name,
        )
        path = tmp_path / "streams" / stream["key"] / f"{unit:04d}"
        for frame in range(5):
            saved = np.concatenate(
                [
                    load_array(path, item)
                    for item in receipt["segments"]
                    if item["frame"] == frame
                ]
            )
            np.testing.assert_array_equal(saved, arrays[frame])
        saved_blocks = np.concatenate(
            [load_array(path, item) for item in block_segments(receipt)]
        )
        np.testing.assert_array_equal(saved_blocks, blocks)
        restored = restore(tmp_path, stream, unit)
        assert restored.counts == context.counts
        for before, after in zip(context.tails, restored.tails):
            np.testing.assert_array_equal(before, after)
        np.testing.assert_array_equal(restored.pending, context.pending)
        if unit:
            assert len(receipt["files"]) == 1
            assert max(item["bytes"] for item in receipt["files"]) <= LIMIT
            # A stale legacy artifact must never override a packed receipt.
            (path / "context.npz").write_bytes(b"stale abandoned attempt")
            assert restore(tmp_path, stream, unit).counts == context.counts
    sample = read_window(tmp_path, stream["key"], 340, 0, [])
    assert sample["end_us"].shape == (1, 64)
    np.testing.assert_array_equal(
        sample["end_us"][0], CUTOFF + FRAMES[0] * np.arange(278, 342)
    )
    payload = path / receipt["files"][0]["name"]
    with payload.open("r+b") as handle:
        handle.seek(-1, 2)
        handle.write(b"\xff")
    with pytest.raises(ValueError, match="mutation"):
        restore(tmp_path, stream, 1)


def test_packed_containers_are_small_and_mappable_at_exact_limit(tmp_path):
    """An exact-limit array and a following array use two bounded physical files."""
    writer = Writer(tmp_path)
    values = np.arange(LIMIT // 8, dtype=np.int64)
    first = writer.add(values, "clock")
    second = writer.add(np.asarray([42], dtype=np.int64), "clock")
    empty = writer.add(np.empty(0, dtype=np.int64), "clock")
    writer.flush()
    assert (
        len(writer.files) == 2 and max(item["bytes"] for item in writer.files) <= LIMIT
    )
    np.testing.assert_array_equal(load(tmp_path, first), values)
    np.testing.assert_array_equal(load(tmp_path, second), [42])
    assert load(tmp_path, empty).size == 0

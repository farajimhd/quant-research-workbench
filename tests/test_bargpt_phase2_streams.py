"""Causal stream geometry, append-only recovery and sampling invariants."""

import json
from pathlib import Path

import numpy as np

from research.bar_gpt.v5.phase2_shards.contract import (
    BAR,
    BLOCK,
    CUTOFF,
    FRAMES,
    SPECS,
    Context,
    windows,
)
from research.bar_gpt.v5.phase2_shards.storage import Catalog, publish, restore
from research.bar_gpt.v5.phase2_shards.sql import query


def bars(ends):
    result = np.zeros(len(ends), dtype=BAR)
    result["values"] = 1
    result["valid"] = 1
    result["end_us"] = ends
    return result


def test_causal_asof_across_chunks_and_full_token_geometry():
    # Give every frame a complete history, then include future coarse bars in
    # the same response as early 1s decisions. The early decisions cannot see it.
    context = Context()
    warm = [
        bars(CUTOFF - 10_000_000_000_000 + np.arange(width) * frame)
        for frame, width, _ in SPECS
    ]
    context.append(warm, decisions=False)
    first = CUTOFF + 100_000_000
    current = [bars(first + np.arange(400) * FRAMES[0])]
    current += [bars(first + np.arange(3) * frame) for frame in FRAMES[1:]]
    blocks, splits = context.append(current)
    assert blocks.shape == (1, BLOCK)
    assert len(context.pending) == 144
    total = 0
    for i, (frame, _, _) in enumerate(SPECS):
        times = np.concatenate((warm[i]["end_us"], current[i]["end_us"]))
        latest = blocks["latest"][:, :, i].ravel()
        assert np.all(times[latest] <= blocks["end_us"].ravel())
        indices, present = windows(latest, i)
        assert present.all()
        total += indices.shape[1]
    assert total == 38
    assert splits.tolist() == [2]  # Daily history crosses the split: purge it.


def test_chunked_and_unbroken_decisions_are_identical():
    whole, chunks = Context(), Context()
    all_bars = [
        bars(CUTOFF - 1_000_000_000 + np.arange(900) * frame) for frame in FRAMES
    ]
    expected, expected_split = whole.append(all_bars)
    output, labels = [], []
    # Irregular network batches must not change full-block boundaries or refs.
    for left, right in ((0, 83), (83, 309), (309, 900)):
        part = []
        begin = all_bars[0]["end_us"][left]
        end = all_bars[0]["end_us"][right - 1]
        for frame, array in zip(FRAMES, all_bars):
            take = (array["end_us"] >= begin) & (array["end_us"] <= end)
            part.append(array[take])
        result, split = chunks.append(part)
        output.extend(result)
        labels.extend(split)
    # Restrict comparison to realistic responses, whose coarser endpoints are
    # included exactly once when their corresponding clock range is fetched.
    all_visible = [a[a["end_us"] <= all_bars[0]["end_us"][-1]] for a in all_bars]
    expected, expected_split = Context().append(all_visible)
    np.testing.assert_array_equal(np.asarray(output), expected)
    np.testing.assert_array_equal(labels, expected_split)


def test_receipt_recovery_preserves_context_and_exact_75_25(tmp_path):
    context = Context()
    arrays = [bars(CUTOFF + 10**13 + np.arange(1024) * f) for f in FRAMES]
    blocks, _ = context.append(arrays)
    receipts = []
    for liquid, name in ((1, "liquid"), (0, "sparse")):
        stream = dict(key=name, ticker=name, year=2025, liquid=liquid)
        receipts.append(
            publish(
                tmp_path,
                stream,
                0,
                arrays,
                [0] * 5,
                blocks,
                np.zeros(len(blocks), dtype=np.uint8),
                context,
                {},
            )
        )
        resumed = restore(tmp_path, stream, 0)
        assert resumed.counts == context.counts
        np.testing.assert_array_equal(resumed.pending, context.pending)
    catalog = Catalog(tmp_path)
    # A staged receipt is not a publication; recovery must ignore it even when
    # a crash happened immediately before the atomic directory rename.
    staged = tmp_path / "streams" / "unpublished" / "0000.partial"
    staged.mkdir(parents=True)
    (staged / "receipt.json").write_text("{}")
    catalog.recover()
    first = catalog.stats()
    catalog.recover()  # Re-registering receipts must not select duplicate IDs.
    assert catalog.stats() == first
    selected = {r["liquid"]: r["selected"] for r in first["blocks"]}
    assert selected == {0: 1, 1: 3}
    assert (
        catalog.db.execute(
            "SELECT count(DISTINCT selection) FROM blocks WHERE selection IS NOT NULL"
        ).fetchone()[0]
        == 4
    )
    catalog.db.close()
    # Exercise the real reader with the canonical action catalog's identity.
    from research.bar_gpt.v5.phase2_shards.audit import content

    (tmp_path / "plan.json").write_text(
        json.dumps(
            dict(
                code={},
                actions=dict(
                    rows=[
                        dict(
                            provider_ticker="liquid",
                            execution_date="2023-01-01",
                            split_from=2,
                            split_to=1,
                        )
                    ]
                ),
            )
        )
    )
    assert len(content(tmp_path)["receipts"]) == 2


def test_selection_adapter_changes_only_explicit_boundaries():
    from research.bar_gpt.v5.shards.range_sql import range_query
    from research.bar_gpt.v5.shards.sql import clock

    rows = [dict(source_date="2023-06-01", first_ordinal=0, next_ordinal=100)]
    original = range_query("AAPL", rows, [], 0, 100)
    expected = original.replace(
        "[100000,1000000,10000000,60000000]", "[1000000,10000000,60000000]"
    )
    expected = expected.replace(
        "(SELECT minIf(sip_timestamp_us,core=1) FROM decoded)",
        str(clock("2023-06-01", 4)),
    )
    expected = expected.replace(
        "(SELECT maxIf(sip_timestamp_us,core=1) FROM decoded)",
        str(clock("2023-06-01", 20)),
    )
    assert query("AAPL", rows, []) == expected

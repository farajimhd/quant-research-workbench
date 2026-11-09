"""Exact4096 shared-context geometry, causal windows and durable restart tests."""

from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
import json
import multiprocessing as mp
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from research.bar_gpt.v5.phase2_ar_shards.contract import (
    BAR,
    BLOCK,
    FRAMES,
    Compiler,
    Geometry,
    validate_bars,
)
from research.bar_gpt.v5.phase2_ar_shards.storage import Catalog, MonthReader, publish
from research.bar_gpt.v5.phase2_ar_shards.source import (
    CanonicalReader,
    describe,
    partition_page,
)
from research.bar_gpt.v5.phase2_ar_shards.run import main
from research.bar_gpt.v5.shards.contract import digest
from research.bar_gpt.v5.shards.sql import clock

BASE = clock("2023-01-03", 4) - 1_000_000


def bars(ends):
    a = np.zeros(len(ends), dtype=BAR)
    a["end_us"] = ends
    a["valid"] = 1
    for side, price in ((0, 10), (6, 9.99), (12, 10.01)):
        a["values"][:, side : side + 4] = price
        a["values"][:, side + 4] = 100
        a["values"][:, side + 5] = 2
    return a


def seeded(geometry=Geometry((1024, 512, 512, 256, 128))):
    c = Compiler(geometry)
    c.bootstrap(
        [
            bars(BASE - np.arange(h - 1, -1, -1) * f)
            for h, f in zip(geometry.history, FRAMES)
        ]
    )
    return c


def new_rows(start, count, coarse=False):
    return [bars(BASE + np.arange(start + 1, start + count + 1) * FRAMES[0])] + [
        (
            bars(BASE + np.arange(1, count * FRAMES[0] // f + 2) * f)
            if coarse
            else np.empty(0, dtype=BAR)
        )
        for f in FRAMES[1:]
    ]


def save(root, c, start, count, month="2023-01", liquid=1, coarse=False):
    compiled = c.compile(new_rows(start, count, coarse), liquid)
    receipt = publish(
        root,
        "TEST",
        month,
        liquid,
        "binding",
        digest([start, count]),
        compiled,
        c.geometry,
    )
    return root / "shards" / digest("TEST")[:24] / month, receipt, compiled


def spawned_month(root):
    """Spawn uses the same CPU compiler/publication/reader implementations."""
    path, receipt, _ = save(Path(root), seeded(), 0, 4100, coarse=True)
    reader = MonthReader(path, "binding")
    block = reader.block(0)
    return (
        receipt["origins"],
        block.windows([0, 4095], 0, actions=()).shape,
        reader.restore().fingerprint(),
    )


def test_every_origin_has_complete_causal_cached_context(tmp_path):
    c = seeded()
    path, r, _ = save(tmp_path, c, 0, 4100, coarse=True)
    reader = MonthReader(path, "binding")
    block = reader.block(0)
    assert r["origins"] == BLOCK and r["stats"]["pending_origins"] == 4
    for frame, history in enumerate(c.geometry.history):
        ends = block.views[frame]["end_us"][block.latest[:, frame]]
        assert np.all(ends <= block.ends)
        expected = (
            np.searchsorted(reader.view(frame)["end_us"], block.ends, side="right") - 1
        )
        np.testing.assert_array_equal(ends, reader.view(frame)["end_us"][expected])
        for origin in (0, 1, 2048, 4095):
            context = block.context(origin, frame)
            assert len(context) == history
            assert context["end_us"][-1] <= block.ends[origin]
    np.testing.assert_array_equal(
        block.views[0]["end_us"][block.latest[:, 0]], block.ends
    )
    # 4096 overlapping contexts share ~5120 1s rows, not4096*1024 copies.
    assert len(block.views[0]) == 4096 + 1024 - 1
    assert reader.restore().fingerprint() == c.fingerprint()


def test_month_boundary_and_restart_byte_exact(tmp_path):
    uninterrupted = seeded()
    path, r, _ = save(tmp_path, uninterrupted, 0, 2000)
    assert r["blocks"] == 0
    recovered = MonthReader(path, "binding").restore()
    assert recovered.fingerprint() == uninterrupted.fingerprint()
    a = uninterrupted.compile(new_rows(2000, 2500), 1)
    b = recovered.compile(new_rows(2000, 2500), 1)
    assert a["blocks"].tobytes() == b["blocks"].tobytes()
    assert len(b["blocks"]) == 1 and len(b["pending"]) == 404
    r = publish(
        tmp_path, "TEST", "2023-02", 1, "binding", "next", b, recovered.geometry
    )
    directory = path.with_name("2023-02")
    block = MonthReader(directory).block(0)
    assert block.ends[0] == BASE + FRAMES[0]
    assert block.ends[-1] == BASE + 4096 * FRAMES[0]
    assert MonthReader(directory).restore().fingerprint() == recovered.fingerprint()


def test_warmup_is_counted_and_cannot_create_incomplete_blocks():
    c = Compiler()
    out = c.compile(new_rows(0, 5000), 1)
    assert out["stats"]["warmup_deferred"] == 5000
    assert not len(out["blocks"]) and not len(out["pending"])
    assert sum(map(len, out["views"])) == 5000


def test_liquidity_boundary_defers_pending_without_mixing():
    c = seeded()
    c.compile(new_rows(0, 2000), 1)
    out = c.compile(new_rows(2000, 5000), 0)
    assert out["stats"]["boundary_tail_deferred"] == 2000
    assert out["blocks"]["end_us"][0, 0] == BASE + 2001 * FRAMES[0]
    assert out["pending_key"] == (0, 0)


def test_origin_split_permits_completed_earlier_year_context():
    geometry = Geometry((64,) * 5, cutoff=BASE + 2048 * FRAMES[0])
    c = seeded(geometry)
    out = c.compile(new_rows(0, 7000), 1)
    assert out["stats"]["boundary_tail_deferred"] == 2047
    assert out["splits"] == [1]  # Prior-year context is valid for a later origin.


def test_overlap_stride_and_common_origin_split_basis(tmp_path):
    path, _, _ = save(tmp_path, seeded(), 0, BLOCK)
    block = MonthReader(path).block(0)
    a = block.windows([0, 4095], 0, actions=(), stride=64)
    b = block.windows([0, 4095], 0, actions=(), stride=32)
    assert a.shape == (2, 16) and b.shape == (2, 31)
    np.testing.assert_array_equal(a["end_us"][:, -1], b["end_us"][:, -1])
    assert a["end_us"][0, 1, 0] - a["end_us"][0, 0, 0] == 64 * FRAMES[0]
    assert b["end_us"][0, 1, 0] - b["end_us"][0, 0, 0] == 32 * FRAMES[0]
    adjusted = block.windows(
        [0], 3, actions=[dict(execution_date="2023-01-03", split_from=1, split_to=2)]
    )
    np.testing.assert_allclose(adjusted["values"][0, 0, :, 0], 5)
    np.testing.assert_allclose(adjusted["values"][0, 0, :, 6], 9.99 / 2)
    np.testing.assert_allclose(adjusted["values"][0, 0, :, 10], 200)
    np.testing.assert_allclose(adjusted["values"][0, 0, :, 11], 2)


@pytest.mark.parametrize(
    "kwargs", [dict(stride=0), dict(stride=1, count=10000), dict(stride=1.5)]
)
def test_invalid_tokenizer_geometry_rejected(tmp_path, kwargs):
    path, _, _ = save(tmp_path, seeded(), 0, BLOCK)
    with pytest.raises(ValueError):
        MonthReader(path).block(0).windows([0], 0, actions=(), **kwargs)


@pytest.mark.parametrize("origins", [[-1], [4096], [0.5]])
def test_invalid_origin_indices_rejected(tmp_path, origins):
    path, _, _ = save(tmp_path, seeded(), 0, BLOCK)
    with pytest.raises(IndexError):
        MonthReader(path).block(0).windows(origins, 0, actions=())


@pytest.mark.parametrize("mutation", ["clock", "nan", "mask", "trade"])
def test_malformed_bars_fail_closed(mutation):
    a = bars(np.array([BASE, BASE + 1]))
    if mutation == "clock":
        a["end_us"][1] = BASE
    elif mutation == "nan":
        a["values"][0, 0] = np.nan
    elif mutation == "mask":
        a["valid"][0, 0] = 2
    else:
        a["values"][0, 0] = 0
    with pytest.raises(ValueError):
        validate_bars(a)


def test_atomic_identity_and_payload_mutation(tmp_path):
    c = seeded()
    path, receipt, compiled = save(tmp_path, c, 0, BLOCK)
    assert (
        publish(
            tmp_path,
            "TEST",
            "2023-01",
            1,
            "binding",
            digest([0, BLOCK]),
            compiled,
            c.geometry,
        )
        == receipt
    )
    with pytest.raises(ValueError):
        publish(tmp_path, "TEST", "2023-01", 1, "other", "source", compiled, c.geometry)
    with (path / "origins.npy").open("ab") as out:
        out.write(b"mutation")
    with pytest.raises(ValueError, match="mutation"):
        MonthReader(path).block(0)


def test_catalog_exact_mix_restart_and_constant_time_progress(tmp_path):
    cat = Catalog(tmp_path)
    for ticker, liquid, blocks in (("A", 1, 7), ("B", 0, 3)):
        r = dict(
            ticker=ticker,
            month="2023-01",
            hash=ticker,
            bytes=100,
            liquid=liquid,
            splits=[0] * blocks,
            blocks=blocks,
            stats=dict(
                source_origins=blocks * BLOCK + 3,
                warmup_deferred=0,
                boundary_tail_deferred=0,
                pending_origins=3,
            ),
        )
        cat.register(tmp_path / ticker, r)
        cat.register(tmp_path / ticker, r)
    expected = cat.stats()
    assert expected["selected_blocks"] == 8 and expected["selected_origins"] == 32768
    assert (
        expected["pending_tail_origins"] == 6
        and expected["unselected_complete_blocks"] == 2
    )
    selection = cat.export("binding")
    assert selection["origins"] == 8 * BLOCK
    # Polling uses only counters; no count(*) table scans per two-second tick.
    cat.db.set_authorizer(lambda *_: 1)
    assert cat.stats() == expected
    cat.db.set_authorizer(None)
    cat.db.close()
    resumed = Catalog(tmp_path)
    assert resumed.stats() == expected
    resumed.db.close()


def test_catalog_empty_export_and_crossing_blocks_unselected(tmp_path):
    c = Catalog(tmp_path)
    assert c.export("binding")["blocks"] == 0
    c.register(
        tmp_path / "A",
        dict(
            ticker="A",
            month="2023-01",
            hash="a",
            bytes=0,
            liquid=1,
            splits=[2],
            blocks=1,
            stats=dict(
                source_origins=BLOCK,
                warmup_deferred=0,
                boundary_tail_deferred=0,
                pending_origins=0,
            ),
        ),
    )
    assert c.stats()["selected_blocks"] == 0
    c.db.close()


@pytest.mark.parametrize(
    "dates,months",
    [
        (["2023-12-29", "2024-01-02"], "202312,202401"),
        (["2023-03-10", "2023-03-13"], "202303"),
        (["2023-11-03", "2023-11-06"], "202311"),
        (["2023-01-31"], "202301,202302"),
    ],
)
def test_partition_filter_covers_complete_ny_sessions(dates, months):
    sql = "SELECT * FROM market_sip_compact.events_2023 PREWHERE ticker='TEST' AND ordinal>=0"
    out = partition_page(sql, [dict(source_date=d) for d in dates])
    assert f"toYYYYMM(event_date) IN ({months}) AND ticker=" in out
    assert out.replace(f"toYYYYMM(event_date) IN ({months}) AND ", "") == sql


def test_unknown_query_shape_fails_closed():
    with pytest.raises(ValueError):
        partition_page("SELECT 1", [dict(source_date="2023-01-01")])


def test_preview_unreleased_parent_is_explicit_and_creates_nothing(tmp_path):
    parent, output = tmp_path / "parent", tmp_path / "output"
    parent.mkdir()
    (parent / "contract.json").write_text("{}")
    with pytest.raises(ValueError, match="not released"):
        main(
            [
                "--parent",
                str(parent),
                "--output",
                str(output),
                "--source-revision",
                "test",
            ]
        )
    assert not output.exists()


def test_spawned_cpu_runnable_publication_path(tmp_path):
    with ProcessPoolExecutor(1, mp_context=mp.get_context("spawn")) as pool:
        origins, shape, fingerprint = pool.submit(spawned_month, str(tmp_path)).result(
            timeout=30
        )
    assert origins == BLOCK and shape == (2, 16) and len(fingerprint) == 64


def test_canonical_reader_fetches_both_fine_and_coarse_with_five_outputs():
    import polars as pl
    from research.bar_gpt.v5.phase2_ar_shards import source

    reader = object.__new__(CanonicalReader)
    reader.plan, reader.client = dict(authority=dict(rules={})), object()
    page = [dict(source_date="2023-01-03", event_count=10)]
    table = pl.DataFrame({"resolution": list(FRAMES)})

    def unpack(rows):
        a = bars(np.repeat(BASE, len(rows)))
        return a["values"], a["valid"], a["end_us"]

    with patch.object(source, "bar_query", return_value="SQL") as query, patch.object(
        source, "partition_page", side_effect=lambda sql, _: sql
    ), patch.object(source.sip, "arrow", return_value=table), patch.object(
        source, "unpack", side_effect=unpack
    ):
        arrays, timing = reader.fetch("TEST", page, nullcontext, 100)
    assert [len(a) for a in arrays] == [1] * 5 and timing["queries"] == 2
    assert [call.args[3] for call in query.call_args_list] == [False, True]


def test_selection_tamper_cannot_be_resealed(tmp_path):
    cat = Catalog(tmp_path)
    selection = cat.export("binding")
    import polars as pl

    pl.DataFrame(
        dict(path=["foreign"], ordinal=[0], split=[0], liquid=[0], selection=[0])
    ).write_parquet(tmp_path / selection["path"])
    with pytest.raises(ValueError, match="selection changed"):
        cat.export("binding")
    cat.db.close()


def test_origin_coverage_cannot_silently_drop_records(tmp_path):
    cat = Catalog(tmp_path)
    cat.register(
        tmp_path / "A",
        dict(
            ticker="A",
            month="2023-01",
            hash="a",
            bytes=0,
            liquid=1,
            splits=[],
            blocks=0,
            stats=dict(
                source_origins=10,
                warmup_deferred=4,
                boundary_tail_deferred=0,
                pending_origins=5,
            ),
        ),
    )
    with pytest.raises(ValueError, match="coverage does not reconcile"):
        cat.stats()
    cat.db.close()

"""Session-only origins, immutable split-basis snapshots and rolling raw cache."""

from contextlib import nullcontext
from datetime import date, timedelta, datetime, timezone
import json
from pathlib import Path
import numpy as np
import polars as pl
import pytest
from research.bar_gpt.v5.phase2_ar_shards.contract import BAR, BLOCK, FRAMES, Geometry
from research.bar_gpt.v5.phase2_ar_shards.sampling import (
    compile_sessions,
    origin_clock,
    rebase_cache,
)
from research.bar_gpt.v5.phase2_ar_shards.inventory import (
    action_chain,
    units_from_index,
)
from research.bar_gpt.v5.phase2_ar_shards.storage import publish, MonthReader, Catalog
from research.bar_gpt.v5.phase2_ar_shards.run import arguments, main
from research.bar_gpt.v5.shards.contract import digest, seed_for
from research.bar_gpt.v5.shards.sql import clock


def us(text):
    return int(
        datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1e6
    )


def bars(ends, price=10):
    a = np.zeros(len(ends), dtype=BAR)
    a["end_us"] = ends
    a["valid"] = 1
    for i, p in ((0, price), (6, price * 0.999), (12, price * 1.001)):
        a["values"][:, i : i + 4] = p
        a["values"][:, i + 4] = 100
        a["values"][:, i + 5] = 2
    return a


class Reader:
    def __init__(
        self, days=("2025-11-03", "2025-11-04"), counts=(5000, 5000), prices=(10, 10)
    ):
        self.days, self.calls = days, []
        prior = (date.fromisoformat(days[0]) - timedelta(days=1)).isoformat()
        self.prior = prior
        self.history = [
            bars(clock(prior, 20) - np.arange(63, -1, -1) * f) for f in FRAMES[:-1]
        ] + [bars(clock(prior, 16) - np.arange(63, -1, -1) * FRAMES[-1])]
        self.data = {}
        for day, n, p in zip(days, counts, prices):
            opened = clock(day, 4)
            current = [bars(opened + np.arange(1, n + 1) * FRAMES[0], p)]
            for f in FRAMES[1:4]:
                current.append(
                    bars(opened + np.arange(1, (n * FRAMES[0] + f - 1) // f + 1) * f, p)
                )
            current.append(
                bars([clock(day, 16)], p) if n >= 6 * 3600 else np.empty(0, dtype=BAR)
            )
            self.data[day] = current

    def months(self, job):
        rows = [
            dict(
                source_date=self.prior,
                first_sip_timestamp_us=clock(self.prior, 4),
                last_sip_timestamp_us=clock(self.prior, 20),
            )
        ]
        rows += [
            dict(
                source_date=d,
                first_sip_timestamp_us=clock(d, 4),
                last_sip_timestamp_us=clock(d, 20),
            )
            for d in self.days
        ]
        return [("metadata", rows)]

    def pages(self, rows, chunk_events):
        if rows:
            yield rows

    def fetch(self, ticker, rows, gate, chunk_events=0, frames=tuple(range(5))):
        self.calls.append(([r["source_date"] for r in rows], frames))
        sources = [
            (
                self.history
                if r["source_date"] == self.prior
                else self.data[r["source_date"]]
            )
            for r in rows
        ]
        return [
            (
                np.concatenate([s[i] for s in sources])
                if i in frames
                else np.empty(0, dtype=BAR)
            )
            for i in range(5)
        ], dict(query_seconds=0.01, queries=1)


def unit(reader):
    return dict(
        ticker="TEST",
        month=reader.days[0][:7],
        first_us=clock(reader.days[0], 4),
        last_us=clock(reader.days[0], 4),
        liquid=1,
        split=int(reader.days[0][:4] == "2026"),
    )


def compile(reader, actions=(), seed=20261109):
    return compile_sessions(
        reader,
        dict(ticker="TEST"),
        unit(reader),
        gate=nullcontext,
        actions=actions,
        seed=seed,
    )


def save(root, item):
    session, out, _ = item
    receipt = publish(
        root, "TEST", session["session"], 1, "binding", digest(session), out, Geometry()
    )
    return root / "shards" / digest("TEST")[:24] / session["session"], receipt


def test_default_warmup_is64_and_no_saved_token_stride():
    assert Geometry().history == (64,) * 5
    assert "token_width" not in Geometry().manifest()
    args = arguments(["--source-revision", "test"])
    assert (
        args.workers,
        args.io_workers,
        args.chunk_events,
        args.validation_bytes,
    ) == (32, 8, 0, 250_000_000)


def test_short_sessions_never_combine_but_update_context():
    reader = Reader(counts=(3000, 3000))
    snapshots = list(compile(reader))
    assert len(snapshots) == 2 and all(not len(s[1]["blocks"]) for s in snapshots)
    assert all(
        s[1]["sampling"]["reason"] == "session_has_fewer_than4096_origins"
        for s in snapshots
    )
    assert (
        len(reader.calls) == 1
    )  # No pointless coarse/history reads for all-short months.
    reader = Reader(counts=(3000, 5000), prices=(20, 30))
    second = list(compile(reader))[1][1]
    # No new hour has closed at the second session's first feasible early origin.
    # Its last completed hourly context comes from the SKIPPED previous session.
    assert second["views"][3]["values"][63, 0] == 20
    assert len(reader.calls) == 3  # Fine/coarse monthly reads plus one shared warmup.


def test_blocks_have_one_session_and_shared_sparse_coarse_arrays(tmp_path):
    reader = Reader(counts=(10000, 5000))
    chosen = next(
        seed
        for seed in range(100)
        if seed_for(seed, "TEST", reader.days[0], "origin") % (10000 - BLOCK + 1)
        <= 10000 - 2 * BLOCK
    )
    snapshots = list(compile(reader, seed=chosen))
    first = snapshots[0][1]
    assert len(first["blocks"]) == 2
    assert len(first["views"][1]) < len(first["views"][0]) / 5
    np.testing.assert_array_equal(
        np.diff(first["blocks"]["end_us"].reshape(-1)), np.repeat(FRAMES[0], 8191)
    )
    directory, receipt = save(tmp_path, snapshots[0])
    block = MonthReader(directory).block(1)
    assert all(len(block.context(0, f)) == 64 for f in range(5))
    assert np.all(block.ends > clock(reader.days[0], 4)) and np.all(
        block.ends <= clock(reader.days[0], 20)
    )
    cat = Catalog(tmp_path)
    cat.register(directory, receipt)
    assert cat.stats()["reconciled_source_origins"] == first["stats"]["source_origins"]
    cat.db.close()


def test_forward_reverse_split_snapshots_are_immutable_and_not_double_adjusted(
    tmp_path,
):
    actions = [
        dict(execution_date="2025-11-04", split_from=7, split_to=1),
        dict(execution_date="2025-11-05", split_from=1, split_to=3),
    ]
    reader = Reader(
        days=("2025-11-03", "2025-11-04", "2025-11-05"),
        counts=(5000, 5000, 5000),
        prices=(10, 70, 70 / 3),
    )
    iterator = compile(reader, actions)
    first = next(iterator)
    before = first[1]["views"][3].tobytes()
    path, _ = save(tmp_path, first)
    from research.bar_gpt.v5.shards.contract import file_hash

    original_hash = file_hash(path / "view-3-0000000000.npy")
    second = next(iterator)
    third = next(iterator)
    assert first[1]["views"][3].tobytes() == before
    assert file_hash(path / "view-3-0000000000.npy") == original_hash
    expected = rebase_cache(reader.history[4], actions, clock("2025-11-05", 4))
    np.testing.assert_array_equal(third[1]["views"][4][:64], expected)
    assert second[1]["views"][4]["values"][0, 0] == 70
    np.testing.assert_array_equal(
        second[1]["views"][4]["values"][:, 5], reader.history[4]["values"][:, 5]
    )
    np.testing.assert_allclose(second[1]["views"][4]["values"][0, 10], 100 / 7)
    directory, _ = save(tmp_path, second)
    block = MonthReader(directory).block(0)
    packed = block.windows([0], 3, actions=actions)
    assert packed["values"][0, 0, -1, 0] == 70  # Not490 from applying the split twice.
    with pytest.raises(ValueError, match="action catalog differs"):
        block.windows([0], 3, actions=())


def test_overnight10s_reference_advances_only_at_close(tmp_path):
    reader = Reader(days=("2025-11-03",), counts=(4096,), prices=(10,))
    # N==4096 forces the first origin to the first observed second.
    item = next(compile(reader))
    directory, _ = save(tmp_path, item)
    block = MonthReader(directory).block(0)
    assert block.context(0, 1)["end_us"][-1] == clock(reader.prior, 20)
    assert block.context(8, 1)["end_us"][-1] == clock(reader.prior, 20)
    assert block.context(9, 1)["end_us"][-1] == clock(reader.days[0], 4) + 10_000_000
    assert block.context(19, 1)["end_us"][-1] == clock(reader.days[0], 4) + 20_000_000


def test_december31_after_utc_midnight_stays_training_session(tmp_path):
    reader = Reader(days=("2025-12-31",), counts=(57600,), prices=(10,))
    seed = next(
        s
        for s in range(10000)
        if seed_for(s, "TEST", "2025-12-31", "origin") % (57600 - BLOCK + 1) > 53000
    )
    item = next(compile(reader, seed=seed))
    assert item[1]["splits"] == [0]
    assert np.any(item[1]["blocks"]["end_us"] >= us("2026-01-01T00:00:00"))
    path, _ = save(tmp_path, item)
    assert MonthReader(path).block(0).split == 0
    index = tmp_path / "index.parquet"
    pl.DataFrame(
        dict(
            ticker=["TEST"],
            source_date=["2025-12-31"],
            first_sip_timestamp_us=[clock("2025-12-31", 4)],
            last_sip_timestamp_us=[clock("2025-12-31", 20)],
        )
    ).write_parquet(index)
    result = units_from_index(
        [index], pl.DataFrame(dict(ticker=["TEST"], year=[2025], liquid=[1])), 11
    )
    assert result["month"].to_list() == ["2025-12"] and result["split"].to_list() == [0]


def test_missing_early_history_is_deferred_not_padded():
    reader = Reader(days=("2019-01-02",), counts=(5000,), prices=(10,))
    reader.history = [a[:0] for a in reader.history]
    item = next(compile(reader))
    assert not len(item[1]["blocks"]) and item[1]["stats"]["warmup_deferred"] > 0
    assert item[1]["sampling"]["first_eligible_origin_us"] is None


def test_mixed_session_publication_is_rejected_before_writing(tmp_path):
    reader = Reader(days=("2025-11-03",), counts=(4096,), prices=(10,))
    item = next(compile(reader))
    item[1]["blocks"]["end_us"][0, -1] = clock("2025-11-04", 4) + FRAMES[0]
    with pytest.raises(ValueError, match="one session"):
        save(tmp_path, item)
    assert not (tmp_path / "shards").exists()


def test_action_coverage_gap_fails_closed():
    with pytest.raises(ValueError, match="coverage gap"):
        action_chain(
            [dict(coverage_start_date="2026-01-01", coverage_end_date="2026-06-01")],
            "2026-01-01",
            "2026-10-03",
        )


def spawned_production_owner(root):
    from research.bar_gpt.v5.phase2_ar_shards import run

    reader = Reader()
    plan = dict(
        hash="binding",
        seed=20261109,
        geometry=Geometry().manifest(),
        authority=dict(corporate_actions=dict(rows=[])),
    )
    run.STATE = dict(
        root=Path(root),
        plan=plan,
        reader=reader,
        jobs={"TEST": dict(ticker="TEST")},
        settings=dict(chunk_events=0),
    )
    return run.produce(unit(reader))


def test_spawned_owner_transfers_independent_session_snapshots(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing as mp

    with ProcessPoolExecutor(1, mp_context=mp.get_context("spawn")) as pool:
        outputs = pool.submit(spawned_production_owner, str(tmp_path)).result(
            timeout=30
        )
    assert len(outputs) == 2
    for session, result in outputs:
        path, _ = save(tmp_path, (session, result["compiled"], result["timing"]))
        block = MonthReader(path).block(0)
        assert np.all(block.ends > clock(session["session"], 4)) and np.all(
            block.ends <= clock(session["session"], 20)
        )


@pytest.mark.parametrize("budget,published", [(250_000_000, True), (1_500_000, False)])
def test_coordinator_separate_io_budget_and_completed_month_resume(
    tmp_path, monkeypatch, budget, published
):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from research.bar_gpt.v5.phase2_ar_shards import run
    from research.bar_gpt.v5 import quote_repair_v1

    reader = Reader(days=("2026-01-02",), counts=(5000,), prices=(10,))
    selected = unit(reader)
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "repair-contract.json").write_text("{}")
    output = tmp_path / "output"
    output.mkdir()
    pl.DataFrame([selected]).write_parquet(output / "sample-units.parquet")
    plan = dict(
        hash="binding",
        seed=20261109,
        geometry=Geometry().manifest(),
        jobs=[dict(ticker="TEST")],
        sample_units=dict(name="sample-units.parquet"),
        authority=dict(corporate_actions=dict(rows=[])),
    )

    class FakeReader(Reader):
        def __init__(self, *args):
            super().__init__(days=("2026-01-02",), counts=(5000,), prices=(10,))

    def cpu(workers, **kwargs):
        return ThreadPoolExecutor(
            workers, initializer=kwargs["initializer"], initargs=kwargs["initargs"]
        )

    monkeypatch.setattr(run, "describe", lambda *_: (plan, {}))
    monkeypatch.setattr(run, "freeze", lambda *_: plan)
    monkeypatch.setattr(run, "CanonicalReader", FakeReader)
    monkeypatch.setattr(run, "ProcessPoolExecutor", cpu)
    monkeypatch.setattr(quote_repair_v1, "require_complete", lambda *_: None)
    original = run.publication
    threads = []

    def publication(*args):
        threads.append(threading.current_thread().name)
        return original(*args)

    monkeypatch.setattr(run, "publication", publication)
    command = [
        "--parent",
        str(parent),
        "--output",
        str(output),
        "--source-revision",
        "test",
        "--execute",
        "--only-ticker",
        "TEST",
        "--workers",
        "1",
        "--validation-lanes",
        "1",
        "--io-workers",
        "1",
        "--validation-bytes",
        str(budget),
    ]
    assert main(command) == 0
    result = json.loads((output / "result.json").read_text())
    assert (
        result["candidate_blocks"] == int(published)
        and result["validation_bytes"] < budget
    )
    if not published:
        assert not threads and result["capacity_deferred"] == 1
        return
    assert threads and all(t.startswith("ar-shard-io") for t in threads)
    monkeypatch.setattr(
        FakeReader, "fetch", lambda *_a, **_k: pytest.fail("Requeried completed month")
    )
    assert main(command) == 0


def test_extended_inventory_is_sealed_and_preserves_utc_year_units(
    tmp_path, monkeypatch
):
    from research.bar_gpt.v5.phase2_ar_shards import inventory
    from research.bar_gpt.v5.shards.contract import file_hash

    parent = tmp_path / "parent"
    (parent / "plan").mkdir(parents=True)
    original = pl.DataFrame(
        dict(
            ticker=["TEST"],
            source_date=["2025-12-31"],
            event_count=[10],
            first_ordinal=[1],
            next_ordinal=[11],
            first_sip_timestamp_us=[us("2025-12-31T09:00:00")],
            last_sip_timestamp_us=[us("2026-01-01T00:59:59")],
            build_step=[1],
        )
    )
    path = parent / "plan/2025-00.parquet"
    original.write_parquet(path)
    manifest = dict(
        partitions=[
            dict(
                name=path.name,
                sha256=file_hash(path),
                rows=1,
                bytes=path.stat().st_size,
            )
        ]
    )
    body = dict(
        jobs=[
            dict(
                ticker="TEST",
                years=[dict(year=2025, bucket=0, liquid=1, days=1, events=10)],
            )
        ],
        authority=dict(corporate_actions=dict(rows=[], catalog_hash=digest([]))),
    )
    extra = original.with_columns(
        pl.lit("2026-01-02").alias("source_date"),
        pl.lit(us("2026-01-02T09:00:00")).alias("first_sip_timestamp_us"),
        pl.lit(us("2026-01-02T23:00:00")).alias("last_sip_timestamp_us"),
    )

    def arrow(client, sql, query_id, **kwargs):
        return extra if query_id.endswith("-0") else extra.head(0)

    monkeypatch.setattr(
        inventory, "check_validation_year", lambda *_: dict(verified=True)
    )
    monkeypatch.setattr(inventory.sip, "arrow", arrow)
    monkeypatch.setattr(inventory, "extend_actions", lambda c, a, end: a)
    output = tmp_path / "output"
    output.mkdir()
    plan = inventory.extend_plan(output, parent, body, manifest, object(), 11)
    units = pl.read_parquet(output / "sample-units.parquet")
    assert (
        set(units["month"]) == {"2025-12", "2026-01"} and len(plan["partitions"]) == 17
    )
    inventory.verify_inventory(output, plan)
    with (output / "inventory/plan/2026-00.parquet").open("ab") as out:
        out.write(b"mutation")
    with pytest.raises(ValueError, match="ordinal inventory changed"):
        inventory.verify_inventory(output, plan)

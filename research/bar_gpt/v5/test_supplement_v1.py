"""Exercise supplement diversity, source anti-joins, receipts, resume and joint splitting."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from research.bar_gpt.v5.cohort_v1 import COHORT_VERSION, members
from research.bar_gpt.v5.data_v1 import build_split, load_split
from research.bar_gpt.v5.liquid_universe_v1 import select_universe
from research.bar_gpt.v5.shards.phase1_contract import (
    FRAMES,
    SAMPLE_DTYPE,
    VERSION,
    digest,
    write_json,
)
from research.bar_gpt.v5.shards.storage import BAR_DTYPE, Budget, publish
from research.bar_gpt.v5 import supplement_v1 as extra


def records(count, frame=1000000, start=1704189600000000):
    """Complete synthetic market records [N,64,18], with distinguishable real clocks."""
    data = np.zeros(count, dtype=SAMPLE_DTYPE)
    data["values"], data["valid"], data["timeframe_us"] = 10, 1, frame
    data["end_us"] = start + (np.arange(count)[:, None] * 64 + np.arange(64)) * frame
    return data


def source_plan():
    jobs = []
    for index, ticker in enumerate(("AAPL", "NVDA", "SPY", "QQQ")):
        years = [
            dict(
                year=year,
                days=100,
                events=10000 + index,
                first_ordinal=(year - 2024) * 10000,
                next_ordinal=(year - 2023) * 10000,
                liquid=1,
                threshold=50,
                bucket=0,
            )
            for year in (2024, 2025)
        ]
        jobs.append(dict(ticker=ticker, key=digest(ticker)[:20], years=years))
    return dict(jobs=jobs, partitions=[], years=[2024, 2025])


def test_universe_and_fine_intervals_cover_required_symbols_without_year_overlap():
    plan = source_plan()
    jobs, evidence = select_universe(plan, top=1)
    assert {j["ticker"] for j in jobs} == {"AAPL", "NVDA", "SPY", "QQQ"}
    assert not {"AAPL", "NVDA", "SPY", "QQQ"} - set(evidence["requested_present"])
    work = extra.fine_jobs(jobs, SimpleNamespace(seed=20261108, chunk_events=1000))
    assert len(work) == 8
    for ticker in evidence["tickers"]:
        previous_end = 0
        for item in sorted(
            (r for r in work if r["job"]["ticker"] == ticker), key=lambda r: r["year"]
        ):
            assert item["start"] >= previous_end
            previous_end = min(
                item["maximum"],
                item["start"]
                + int(np.ceil((item["limit"] - item["start"]) / 1000)) * 1000,
            )


def test_parent_anti_join_and_async_quota_publication(tmp_path):
    base, output = tmp_path / "base", tmp_path / "extra"
    job = source_plan()["jobs"][0]
    data = records(4)
    parent_path = base / "jobs" / f"2024-{job['key']}" / "fine-1-1000-1000000"
    publish(
        parent_path,
        data[:1],
        np.empty(0, dtype=BAR_DTYPE),
        dict(
            identity="parent",
            binding="parent-binding",
            ticker="AAPL",
            year=2024,
            liquid=1,
            day="2024-01-02",
        ),
        Budget(10**8),
        32 * 1024**2,
    )
    parent = extra.ParentWindows(base, job, "parent-binding")
    np.testing.assert_array_equal(parent.keep(data, 1000000), [False, True, True, True])
    quota = extra.Allocation()
    quota.targets[1000000] = quota.limits["slot", 1000000] = 2
    status = extra.bars.Status(1)
    pending = []
    args = SimpleNamespace(output=output)
    with ThreadPoolExecutor(2) as disk:
        extra.submit(
            args,
            job,
            "slot",
            "fine-2-1000",
            data,
            "new-binding",
            dict(parent_binding="parent-binding"),
            parent,
            quota,
            Budget(10**8),
            status,
            disk,
            pending,
        )
        for future in pending:
            future.result()
    path = next(output.glob("jobs/*/*/receipt.json"))
    receipt = json.loads(path.read_text())
    assert receipt["samples"] == quota.total(1000000) == 2
    assert quota.reserved(1000000) == 0 and receipt["parent_duplicates_rejected"] == 1
    stored = np.load(path.parent / receipt["files"][0]["name"], allow_pickle=False)
    assert parent.keep(stored, 1000000).all()


def test_joint_cohort_split_purges_overlaps_between_components(tmp_path):
    base, supplement, family = (
        tmp_path / "base",
        tmp_path / "extra",
        tmp_path / "phase1-v6-cohort",
    )
    for root, tag in ((base, "base"), (supplement, "extra")):
        root.mkdir()
        contract = dict(version=VERSION, fixture=tag)
        if tag == "extra":
            contract["parent_binding"] = json.loads(
                (base / "contract.json").read_text()
            )["binding"]
        write_json(
            root / "contract.json", dict(contract=contract, binding=digest(contract))
        )
        for day in range(1, 11):
            start = int(
                datetime(2024, 1, day, 10, tzinfo=timezone.utc).timestamp() * 1e6
            )
            rows = records(1, start=start)
            publish(
                root / "jobs" / "fixture" / str(day),
                rows,
                np.empty(0, dtype=BAR_DTYPE),
                dict(
                    identity=str(day), binding=digest(contract), day=f"2024-01-{day:02}"
                ),
                Budget(10**8),
                1024**2,
            )
        write_json(
            root / "result.json", dict(state="quotas_reached", failed=[], samples=10)
        )
    body = dict(
        version=COHORT_VERSION,
        schema=VERSION,
        members=[
            dict(name="base", path="../base"),
            dict(name="liquid500", path="../extra"),
        ],
    )
    family.mkdir()
    write_json(family / "cohort.json", dict(cohort=body, hash=digest(body)))
    manifest = build_split(base, tmp_path / "joint.json")
    assert manifest["counts"] == {"train": 16, "validation": 4}
    assert len(manifest["dataset_members"]) == 2 and manifest["cohort_hash"] == digest(
        body
    )
    crossing = records(1)
    crossing["end_us"][0] = np.r_[
        int(datetime(2024, 1, 8, 10, tzinfo=timezone.utc).timestamp() * 1e6)
        + np.arange(32) * 1000000,
        int(datetime(2024, 1, 9, 10, tzinfo=timezone.utc).timestamp() * 1e6)
        + np.arange(32) * 1000000,
    ]
    binding = json.loads((supplement / "contract.json").read_text())["binding"]
    publish(
        supplement / "jobs/fixture/crossing",
        crossing,
        np.empty(0, dtype=BAR_DTYPE),
        dict(identity="crossing", binding=binding, day="coarse"),
        Budget(10**8),
        1024**2,
    )
    purged = build_split(base, tmp_path / "purged.json")
    assert purged["counts"] == manifest["counts"] and purged["purged_crossing"] == 1
    assert load_split(tmp_path / "joint.json")["cohort_hash"] == manifest["cohort_hash"]
    write_json(supplement / "result.json", dict(state="stopped", failed=[]))
    with pytest.raises(ValueError, match="not completed successfully"):
        members(base, require_all=True)


def test_real_supplement_orchestration_and_idempotent_resume(tmp_path, monkeypatch):
    """Mock final CH aggregates only; run actual packing, quotas, I/O, checkpoints and resume."""
    base, output = tmp_path / "base", tmp_path / "extra"
    (base / "plan").mkdir(parents=True)
    parent = source_plan()
    parent["hash"] = digest(parent)
    write_json(base / "plan/manifest.json", parent)
    authority = dict(rules=[], corporate_actions=dict(rows=[]))
    body = dict(version=VERSION, authority=authority, code={})
    write_json(base / "contract.json", dict(contract=body, binding=digest(body)))
    args = SimpleNamespace(
        base=base,
        output=output,
        top=1,
        seed=20261108,
        chunk_events=1000,
        coarse_days=20,
        workers=2,
        io_workers=1,
        target_bytes=20_000_000,
        query_memory_gib=1.0,
        endpoint=None,
    )
    monkeypatch.setattr(
        extra.bars.source,
        "client",
        lambda *a: SimpleNamespace(base_url="http://synthetic"),
    )
    monkeypatch.setattr(extra.bars.source, "close", lambda c: None)
    monkeypatch.setattr(extra.bars.source, "preflight", lambda c: dict(rules=[]))
    monkeypatch.setattr(extra.bars.source, "split_authority", lambda *a: dict(rows=[]))
    monkeypatch.setattr(
        extra.bars,
        "ticker_bounds",
        lambda *a: [
            dict(source_date="2024-01-02", first_ordinal=0, next_ordinal=10000),
            dict(source_date="2025-01-02", first_ordinal=10000, next_ordinal=20000),
        ],
    )
    monkeypatch.setattr(
        extra.bars,
        "range_query",
        lambda ticker, bounds, rules, first, end, **kw: dict(
            first=first, coarse=bool(kw)
        ),
    )
    calls = []

    def aggregates(c, statement, *a):
        calls.append(statement)
        first = statement["first"]
        year = 2024 if first < 10000 else 2025
        start = int(datetime(year, 1, 2, tzinfo=timezone.utc).timestamp() * 1e6)
        frames = FRAMES[4:] if statement["coarse"] else FRAMES[:4]
        rows = []
        for frame in frames:
            rows.extend(
                dict(
                    resolution=frame,
                    end_us=start + (first % 10000 + i + 1) * frame,
                    values=[10.0] * 18,
                    valid=[1] * 18,
                )
                for i in range(100)
            )
        return pl.DataFrame(rows)

    monkeypatch.setattr(extra.bars, "execute_range", aggregates)
    assert extra.run(args) == 0
    result = json.loads((output / "result.json").read_text())
    assert result["samples"] > 0 and not result["failed"]
    assert all(result["frames"][str(f)] > 0 for f in FRAMES)
    receipts = list(output.glob("jobs/*/*/receipt.json"))
    hashes = [p.read_bytes() for p in receipts]
    before = len(calls)
    assert extra.run(args) == 0
    assert len(calls) == before and [p.read_bytes() for p in receipts] == hashes
    assert (
        json.loads((output / "result.json").read_text())["samples"] == result["samples"]
    )


def test_queue_waits_for_success_and_a_released_controller_lock(tmp_path):
    """Neither a STOP nor a stale success result may start a competing extraction."""
    from research.bar_gpt.v5.run_liquid_supplement import dependency_ready
    import msvcrt

    lock = tmp_path / "controller.lock"
    lock.write_bytes(b"0")
    for state in ("stopped", "failed"):
        write_json(tmp_path / "result.json", dict(state=state, samples=10, failed=[]))
        assert dependency_ready(tmp_path) is None
    write_json(
        tmp_path / "result.json", dict(state="quotas_reached", samples=10, failed=[])
    )
    write_json(
        tmp_path / "launch.json",
        dict(started=(tmp_path / "result.json").stat().st_mtime + 10),
    )
    assert dependency_ready(tmp_path) is None
    (tmp_path / "launch.json").unlink()
    owner = lock.open("r+b")
    try:
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        assert dependency_ready(tmp_path) is None
    finally:
        owner.close()
    released = dependency_ready(tmp_path)
    assert released is not None
    released.close()

"""Prove scheduling skips cannot discard samples the frozen quotas could admit."""

import json
import threading
from types import SimpleNamespace

import pytest

from research.bar_gpt.v5 import shard_runtime_v1 as runtime
from research.bar_gpt.v5.shards import balanced_build as builder
from research.bar_gpt.v5.shards.phase1_contract import FRAMES, write_json


def remaining_liquid_coarse():
    quota = builder.Quotas(10**8)
    for frame in FRAMES:
        target = quota.targets[str(frame)]
        quota.saved[str(frame)] = [int(target * 0.25), int(target * 0.75)]
    for frame in FRAMES[4:]:
        quota.saved[str(frame)][1] = 0
    return quota


def test_class_room_keeps_pending_and_future_eligible_history():
    quota = remaining_liquid_coarse()
    blocked = dict(years=[dict(year=2024, liquid=0)])
    future = dict(years=[dict(year=2019, liquid=0), dict(year=2025, liquid=1)])
    trailing = dict(years=[dict(year=2019, liquid=1), dict(year=2024, liquid=0)])
    assert not runtime.job_has_room(quota, blocked, FRAMES)
    assert runtime.job_has_room(quota, future, FRAMES[4:], minimum_year=2020)
    assert not runtime.job_has_room(quota, trailing, FRAMES[4:], minimum_year=2020)
    quota.pending[str(FRAMES[-1])][1] = quota.targets[str(FRAMES[-1])]
    assert runtime.job_has_room(
        quota, future, (FRAMES[-1],)
    )  # An uncommitted write is not proof that a quota is filled.


def test_pruning_changes_only_the_independent_resolution_list():
    bounds = [dict(source_date="2024-01-02", first_ordinal=1, next_ordinal=1000)]
    original = builder.range_query(
        "TEST",
        bounds,
        [],
        1,
        1000,
        begin_us=builder.clock("2024-01-02", 4),
        end_us=builder.clock("2024-01-02", 20),
    )
    daily = runtime.coarse_resolutions(original, (FRAMES[-1],))
    hourly = runtime.coarse_resolutions(original, (FRAMES[-2],))
    assert daily.replace("[86400000000]", runtime.COARSE_LITERAL) == original
    assert hourly.replace("[3600000000]", runtime.COARSE_LITERAL) == original
    assert runtime.coarse_resolutions(original, ()) == original
    assert runtime.coarse_resolutions(original, FRAMES[4:]) == original
    with pytest.raises(ValueError, match="declaration changed"):
        runtime.coarse_resolutions("SELECT unexpected", (FRAMES[-1],))


def test_runtime_skips_restore_and_empty_cohorts_without_canonical_work(
    tmp_path, monkeypatch
):
    calls = []
    original_quota = builder.Quotas
    monkeypatch.setattr(
        builder, "worker", lambda *args: calls.append(args[1]["ticker"]) or True
    )
    args = SimpleNamespace(output=tmp_path)
    complete = dict(ticker="FINISHED", key="done", years=[dict(year=2024, liquid=1)])
    blocked = dict(ticker="ILLQ", key="blocked", years=[dict(year=2024, liquid=0)])
    eligible = dict(ticker="LIQ", key="eligible", years=[dict(year=2024, liquid=1)])
    (tmp_path / "checkpoints").mkdir()
    write_json(
        tmp_path / "checkpoints/done.json", dict(binding="binding", complete=True)
    )
    with runtime.scheduling_runtime(builder, tmp_path):
        quota = builder.Quotas(10**8)
        baseline = remaining_liquid_coarse()
        quota.saved = baseline.saved
        status = builder.Status(1)
        for job in (complete, blocked, eligible):
            assert builder.worker(
                0,
                job,
                args,
                {},
                "binding",
                {},
                quota,
                None,
                status,
                None,
                threading.Event(),
            )
        assert calls == ["LIQ"]
        counters = status.snapshot()["optimization"]
        assert (
            counters["restored_complete_jobs"]
            == counters["skipped_full_cohort_jobs"]
            == 1
        )
        record = json.loads(
            next(tmp_path.glob("runtime-optimization-*.json")).read_text()
        )
        assert record["adapter_sha256"] == counters["adapter_sha256"]
        write_json(
            tmp_path / "checkpoints/done.json", dict(binding="foreign", complete=True)
        )
        with pytest.raises(ValueError, match="different campaign"):
            builder.worker(
                0,
                complete,
                args,
                {},
                "binding",
                {},
                quota,
                None,
                status,
                None,
                threading.Event(),
            )
    assert builder.Quotas is original_quota


def test_runtime_keeps_warmup_and_prunes_only_exhausted_frame_groups(
    tmp_path, monkeypatch
):
    observed = []
    bounds = [dict(source_date="2024-01-02", first_ordinal=1, next_ordinal=1000)]

    def worker(
        lane, job, args, plan, binding, authority, quota, budget, status, disk, stop
    ):
        assert quota.needed(
            FRAMES[4:]
        )  # 2024 context is still necessary for a liquid 2025 window.
        statement = builder.range_query(
            job["ticker"],
            bounds,
            [],
            1,
            1000,
            begin_us=builder.clock("2024-01-02", 4),
            end_us=builder.clock("2024-01-02", 20),
        )
        observed.append(statement)
        assert quota.needed(FRAMES[4:])
        builder.range_query(
            job["ticker"],
            bounds,
            [],
            1,
            1000,
            begin_us=builder.clock("2026-01-02", 4),
            end_us=builder.clock("2026-01-02", 20),
        )
        assert not quota.needed(
            FRAMES[4:]
        )  # No eligible end-year remains after 2025; the trailing scan can stop.
        quota.saved[str(FRAMES[0])][1] = 0
        assert quota.needed(
            FRAMES[:4]
        )  # A completed coarse pass cannot hide earlier fine-frame opportunities.
        return True

    monkeypatch.setattr(builder, "worker", worker)
    with runtime.scheduling_runtime(builder, tmp_path):
        quota = builder.Quotas(10**8)
        quota.saved = remaining_liquid_coarse().saved
        quota.saved[str(FRAMES[-2])][1] = int(quota.targets[str(FRAMES[-2])] * 0.75)
        job = dict(
            ticker="TEST",
            key="test",
            years=[dict(year=2024, liquid=0), dict(year=2025, liquid=1)],
        )
        status = builder.Status(1)
        assert builder.worker(
            0,
            job,
            SimpleNamespace(output=tmp_path),
            {},
            "binding",
            {},
            quota,
            None,
            status,
            None,
            threading.Event(),
        )
        assert (
            runtime.COARSE_LITERAL not in observed[0] and "[86400000000]" in observed[0]
        )
        assert status.snapshot()["optimization"]["trailing_history_prunes"] == 1

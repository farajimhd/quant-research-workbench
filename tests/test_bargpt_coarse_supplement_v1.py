import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from research.bar_gpt.v5 import coarse_supplement_v1 as s
from research.bar_gpt.v5.run_coarse_supplement_v1 import arguments


def bars(n, frame=s.COARSE[0]):
    result = np.zeros(n, dtype=s.BAR)
    result["values"] = 10
    result["valid"] = 1
    result["end_us"] = 1_600_000_000_000_000 + np.arange(n)*frame
    return result


def test_overlap_prioritizes_unseen_then_half_context():
    starts = np.arange(161)
    coverage = np.zeros(224, dtype=bool)
    got = s.choose(starts, coverage)
    assert got.tolist() == [0, 64, 128, 32, 96, 160]
    assert np.diff(np.sort(got)).min() == 32
    # Previously used parent context is allowed, but never preferred over unseen.
    coverage[:64] = True
    got = s.choose(starts, coverage)
    assert got[:2].tolist() == [64, 128]
    assert np.diff(np.sort(got)).min() >= 32
    assert s.choose(np.array([], dtype=np.int64), coverage).size == 0
    with pytest.raises(ValueError):
        s.choose(np.array([2, 1]), coverage)


def test_full_context_purge_and_cutoff_boundary():
    ends = np.array([[100, 200], [201, 300], [250, 350]])
    train, val = s.split_masks(ends, 50, 250)
    assert train.tolist() == [True, False, False]
    assert val.tolist() == [False, False, False]
    _, val = s.split_masks(np.array([[300, 400]]), 50, 250)
    assert val.tolist() == [True]


def parent(tmp_path, data, start=0):
    body = {"version": s.VERSION}
    binding = s.digest(body)
    root = tmp_path / "parent"
    root.mkdir(exist_ok=True)
    s.write_json(root / "contract.json", dict(contract=body, binding=binding))
    job = dict(key="key", ticker="TEST", years=[dict(year=2020)])
    directory = root / "jobs/2020-key/coarse-test"
    rows = s.packed(data, np.array([start]), s.COARSE[0], 0, [])
    metadata = dict(identity="parent-unit", binding=binding)
    s.publish(directory, rows, np.empty(0, dtype=s.BAR_DTYPE), metadata, s.Budget(10**8), 2**20)
    return [("base", root)], job, directory


def test_parent_antijoin_exact_clocks_hashes_and_new_only_overlap(tmp_path):
    data = bars(200)
    parents, job, directory = parent(tmp_path, data)
    known, covered = s.parent_index(parents, job, s.COARSE[0], data)
    assert known.tolist() == [0]
    assert np.array_equal(covered, np.arange(200) < 64)
    selection, counts = s.candidates(job, parents, {s.COARSE[0]: data, s.COARSE[1]: bars(0, s.COARSE[1])}, 2**62)
    got = selection["train", s.COARSE[0]]
    assert 0 not in got
    assert np.diff(np.sort(got)).min() >= 32
    assert counts[str(s.COARSE[0])]["parent_windows"] == 1
    payload = next(directory.glob("samples-*.npy"))
    with payload.open("ab") as f:
        f.write(b"bad")
    with pytest.raises(ValueError, match="SHA256|Changed sealed file"):
        s.parent_index(parents, job, s.COARSE[0], data)


def test_parent_interior_clock_conflict_fails(tmp_path):
    data = bars(100)
    parents, job, _ = parent(tmp_path, data)
    different = data.copy()
    different["end_us"][10] += 1
    with pytest.raises(ValueError, match="clocks differ"):
        s.parent_index(parents, job, s.COARSE[0], different)


def test_packed_matches_shared_math_and_does_not_mutate_cache():
    frame = s.COARSE[1]
    data = bars(70, frame)
    raw = data.tobytes()
    actions = [dict(execution_date="2020-10-01", split_from=1, split_to=4)]
    rows = s.packed(data, np.array([0, 6]), frame, 1, actions)
    ix = np.array([0, 6])[:, None] + np.arange(64)
    expected = s.pack(data["values"][ix], data["valid"][ix], data["end_us"][ix], frame, 1)
    s.adjust_splits(expected, actions)
    assert rows.tobytes() == expected.tobytes()
    assert data.tobytes() == raw
    assert np.all(rows["values"][0, :5, 0] == 2.5)
    assert np.all(rows["values"][0, :5, 10] == 40)


class Admission:
    def __init__(self):
        from contextlib import nullcontext
        self.admit = nullcontext


def test_cached_pages_reuse_and_reject_mutation(tmp_path, monkeypatch):
    bounds = [dict(source_date=f"2020-01-{i:02d}", first_ordinal=i*100,
                   next_ordinal=(i+1)*100, event_count=100) for i in [2, 3, 4]]
    monkeypatch.setattr(s, "ticker_bounds", lambda *a: bounds)
    monkeypatch.setattr(s, "range_query", lambda *a, **kw: "SELECT canonical")
    calls = []
    def query(*args, **kwargs):
        calls.append(1)
        offset = len(calls)-1
        return pl.DataFrame(dict(resolution=[s.COARSE[0], s.COARSE[1]],
                                 end_us=[1_600_000_000_000_000+offset*10**12]*2,
                                 values=[[10.]*18]*2, valid=[[1]*18]*2))
    monkeypatch.setattr(s.source, "arrow", query)
    contract = dict(binding="b", contract=dict(configuration=dict(chunk_events=100, chunk_days=1, max_ticker_bars=100), authority=dict(rules=[])))
    job = dict(ticker="TEST", key="key")
    import threading
    progress = s.Progress(tmp_path, 1, 1)
    first = s.cache_ticker(job, tmp_path, tmp_path, contract, None, Admission(), progress, threading.Event())
    again = s.cache_ticker(job, tmp_path, tmp_path, contract, None, Admission(), progress, threading.Event())
    assert len(calls) == 3
    assert first[s.COARSE[0]].tobytes() == again[s.COARSE[0]].tobytes()
    with next((tmp_path / "bar-cache/key").glob("*.npy")).open("ab") as f:
        f.write(b"bad")
    with pytest.raises(ValueError, match="integrity"):
        s.cache_ticker(job, tmp_path, tmp_path, contract, None, Admission(), progress, threading.Event())


def test_whole_dense_session_is_not_truncated():
    bounds = [dict(source_date="2020-01-02", event_count=3_000_000),
              dict(source_date="2020-01-03", event_count=100)]
    assert s.session_batches(bounds, 500_000, 5) == [[bounds[0]], [bounds[1]]]


def test_cli_has_no_default_execution_and_preserves_reserves(tmp_path):
    with pytest.raises(SystemExit):
        arguments(["--source-revision", "x"])
    with pytest.raises(SystemExit):
        arguments(["prepare", "--source-revision", "x", "--runtime-root", str(tmp_path), "--ram-reserve-gib", "1"])
    a = arguments(["prepare", "--source-revision", "x", "--runtime-root", str(tmp_path)])
    assert a.workers == 16 and a.io_workers == 4 and a.chunk_events == 500_000


def test_extraction_real_publication_resume_path(tmp_path, monkeypatch):
    """Actual coordinator, packing, asynchronous publisher, receipts and resume."""
    output = tmp_path / "supplement"
    output.mkdir()
    cached = {f: bars(200, f) for f in s.COARSE}
    jobs = [dict(ticker="TEST", key="key", years=[dict(year=2020, days=100, events=10000)])]
    body = dict(configuration=dict(workers=2, io_workers=2, query_memory_gib=1,
                                  ram_reserve_gib=64, disk_reserve_gib=150, target_bytes=10**8, chunk_events=500000),
                authority=dict(schema=[], dictionary_hash="d", rules=[], corporate_actions=dict(rows=[])),
                targets={name: {str(f): 3 for f in s.COARSE} for name in ["train", "validation"]}, cutoff_us=2**62)
    contract = dict(contract=body, binding=s.digest(body))
    s.write_json(output / "contract.json", contract)
    monkeypatch.setattr(s.source, "client", lambda *a: SimpleNamespace(default_query_params={}))
    monkeypatch.setattr(s.source, "close", lambda *a: None)
    monkeypatch.setattr(s.source, "preflight", lambda *a: body["authority"])
    calls = []
    def build(job, *a):
        calls.append(job["key"])
        selection = {(name, f): np.array([0, 64, 32]) if name == "train" else np.array([], dtype=np.int64)
                     for name in ["train", "validation"] for f in s.COARSE}
        return job, cached, selection, {}
    monkeypatch.setattr(s, "build_job", build)
    result = s.extract(output, contract, dict(partitions=[], jobs=jobs), [("base", tmp_path)])
    assert result["state"] == "inventory_exhausted"
    assert result["shortfall"]["validation"][str(s.COARSE[0])] == 3
    assert s.verify_receipt(output / f"jobs/key/train-{s.COARSE[0]}")["samples"] == 3
    again = s.extract(output, contract, dict(partitions=[], jobs=jobs), [("base", tmp_path)])
    assert again["saved"] == result["saved"] and calls == ["key"]
    (output / "STOP").write_text("HUMAN")
    with pytest.raises(InterruptedError):
        s.extract(output, contract, dict(partitions=[], jobs=jobs), [("base", tmp_path)])
    assert (output / "STOP").read_text() == "HUMAN"


def test_describe_freezes_exact_split_and_targets_without_output(tmp_path, monkeypatch):
    body = dict(version=s.VERSION, authority={}, plan_hash=None,
                quote_quality=s.quote_quality_v1.POLICY, quote_semantics=s.bar_semantics_v1.CONTRACT)
    plan = dict(jobs=[], partitions=[])
    plan["hash"] = s.digest(plan)
    body["plan_hash"] = plan["hash"]
    root = tmp_path / "parent"
    (root / "plan").mkdir(parents=True)
    binding = s.digest(body)
    s.write_json(root / "contract.json", dict(contract=body, binding=binding))
    s.write_json(root / "plan/manifest.json", plan)
    monkeypatch.setattr(s, "members", lambda *a, **kw: ("cohort-hash", [("base", root)]))
    entry = lambda f, n: dict(frames={str(f): n}, start=0, stop=n, rows=n)
    split = dict(cohort_hash="cohort-hash", dataset_members=[dict(name="base", path=str(root), binding=binding)],
                 splits={name: [entry(s.FRAMES[1], 10), entry(s.COARSE[0], 4), entry(s.COARSE[1], 2)] for name in ["train", "validation"]},
                 counts=dict(train=16, validation=16), cutoff_us=12345, cutoff_day="2024-04-16")
    split["manifest_hash"] = s.digest(split)
    split_path = tmp_path / "split.json"
    s.write_json(split_path, split)
    contract, _, _ = s.describe(tmp_path, split_path, {})
    assert contract["contract"]["targets"]["train"] == {str(s.COARSE[0]): 6, str(s.COARSE[1]): 8}
    assert contract["contract"]["cutoff_us"] == 12345
    # Contract JSON roundtrip must remain exactly equal for prepare/extract.
    assert json.loads(json.dumps(contract)) == contract


@pytest.mark.parametrize("state,age", [("running",0),("running",120),("failed",0),("inventory_exhausted",0)])
@pytest.mark.parametrize("width", [80,120])
def test_panel_states_fit_and_do_not_claim_cache_is_durable_windows(state, age, width):
    from io import StringIO
    from rich.console import Console
    from research.bar_gpt.v5.coarse_supplement_panel_v1 import render
    stream = StringIO()
    console = Console(width=width, file=stream, color_system=None)
    data = dict(state=state, updated=100-age, total=100, completed=3, active={"TEST":dict(stage="coarse query/cache",page=2,pages=10)})
    console.print(render(data,dict(targets={"train":{str(s.COARSE[0]):100}}),now=100))
    text = stream.getvalue()
    assert "Durable tickers" in text and "New windows" in text
    assert "STALE" in text if age else "STALE" not in text
    assert max(len(line) for line in text.splitlines()) <= width


def test_exclusive_writer_lease(tmp_path):
    with s.writer_lease(tmp_path):
        with pytest.raises(OSError):
            with s.writer_lease(tmp_path):
                pytest.fail("Second writer acquired lease")
    with s.writer_lease(tmp_path):
        assert s.read(tmp_path / "owner.json")["pid"] > 0

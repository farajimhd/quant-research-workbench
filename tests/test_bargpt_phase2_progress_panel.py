"""Bounded observer lifecycle, payload parity, and real terminal rendering."""

import io
import json
import time
from pathlib import Path

import pytest
from rich.console import Console

from research.bar_gpt.v5.phase2_ar_shards.telemetry import WorkerTelemetry, ObservedReader
from research.bar_gpt.v5.phase2_progress_panel import render, SnapshotReader, worker_state


def snapshot():
    now = time.time()
    records = {}
    for role, count in (("cpu", 32), ("io", 8)):
        for slot in range(count):
            records[role, slot] = dict(
                state="Query" if role == "cpu" else "Write", ticker="AAPL",
                month="2025-02", progress_done=5, progress_total=20,
                completed=12, assigned=13, tickers=9, queries=23,
                bytes_done=42_000_000, task_started=now - 31, pid=1000 + slot,
                age=0, updated=now, cpu_pct=80,
            )
    return dict(now=now, execution=dict(workers=32, io_workers=8),
                progress=dict(state="running", updated=now, bytes=114_000_000_000,
                              train_bytes=113_750_000_000, validation_bytes=250_000_000,
                              planned_units=1_000_000, accounted_months=300_000,
                              units=100_000, selected_origins=80_000_000,
                              active_cpu=32, active_io=8, active_queries=4, query_limit=4),
                workers=records, read_errors=[], rate=5_000_000)


@pytest.mark.parametrize("width,height", [(200, 52), (100, 36), (80, 24)])
def test_render_fits_and_paginates(width, height):
    console = Console(file=io.StringIO(), width=width, height=height, no_color=True)
    with console.capture() as captured:
        console.print(render(snapshot(), width, height, page=0))
    text = captured.get()
    assert len(text.splitlines()) <= height
    assert all(len(line) <= width for line in text.splitlines())
    assert "C01" in text and "I01" in text and "Ctrl+C" in text
    assert "\x1b" not in text
    if width == 200:
        assert all(f"C{i:02d}" in text for i in range(1, 33))
        assert all(f"I{i:02d}" in text for i in range(1, 9))
    else:
        assert "page" in text


def test_failure_and_staleness_remain_visible():
    s = snapshot()
    s["progress"].update(state="failed", failure="Canonical query failed")
    s["workers"]["cpu", 0]["state"] = "Failed"
    s["workers"]["cpu", 1]["age"] = 20
    assert worker_state(s["workers"]["cpu", 1])[0] == "STALE"
    console = Console(file=io.StringIO(), width=200)
    with console.capture() as capture:
        console.print(render(s, 200, 52))
    assert "Canonical query failed" in capture.get() and "FAILED" in capture.get()


def test_restart_does_not_label_prior_attempt_as_current_stop():
    s=snapshot();s['progress'].update(state='capacity_reached',worker_assignments=[dict(slot=0,total=10,completed=3,deferred=2,tickers=1)])
    s['execution']['started']=s['progress']['updated']+1
    console=Console(file=io.StringIO(),width=200)
    with console.capture() as captured:console.print(render(s,200,52))
    assert 'restoring file ledger' in captured.get() and 'STOPPED WITH QUEUED WORK' not in captured.get()


def test_cached_overlap_work_is_not_labelled_as_canonical_queries():
    s=snapshot();s['progress'].update(source_mode='verified parent pools',active_cache_reads=8,active_queries=0)
    console=Console(file=io.StringIO(),width=200,height=52,no_color=True)
    with console.capture() as captured:console.print(render(s,200,52))
    assert 'Verified cache reads 8' in captured.get() and 'Source queries' not in captured.get()


@pytest.mark.parametrize("width,height", [(200,52),(100,36),(80,24)])
def test_separate_capacity_states_fit_terminal(width,height):
    from research.bar_gpt.v5.phase2_ar_shards.run import capacity_status
    s=snapshot();s['execution'].update(target_bytes=4_000_000_000_000,validation_bytes=250_000_000)
    s['progress'].update(state='inventory_exhausted',capacity=capacity_status(
        {0:406_720_703_548,1:248_587_869},{0:0,1:0},{0:4_000_000_000_000,1:250_000_000},{0:0,1:0},{0:0,1:116402}))
    console=Console(file=io.StringIO(),width=width,height=height,no_color=True)
    with console.capture() as captured:console.print(render(s,width,height,page=0))
    text=captured.get()
    assert 'Training headroom' in text and 'Validation cap LIMIT' in text
    assert 'Plan exhausted' in text and 'minimum_block_fit' in text
    assert len(text.splitlines())<=height and all(len(x)<=width for x in text.splitlines())


def test_completed_queue_and_terminal_rate_are_explicit(tmp_path):
    s=snapshot();s['rate']=1_500_000
    s['progress'].update(state='inventory_exhausted',worker_assignments=[dict(slot=0,total=10,completed=3,deferred=7,tickers=1)])
    console=Console(file=io.StringIO(),width=200,height=52,no_color=True)
    with console.capture() as captured:console.print(render(s,200,52,page=0))
    text=captured.get()
    assert 'Done' in text and 'no active transfer' in text and '1.5 MB/s' not in text
    assert 'No queued work' in text and 'final status' in text
    (tmp_path/'execution.json').write_text(json.dumps(dict(workers=0,io_workers=0)))
    reader=SnapshotReader(tmp_path)
    for state,updated,size in [('running',time.time()-5,100),('inventory_exhausted',time.time(),200)]:
        (tmp_path/'progress.json').write_text(json.dumps(dict(state=state,updated=updated,bytes=size)))
        result=reader.read()
    assert result['rate'] is None


@pytest.mark.parametrize('width,height',[(200,52),(80,24)])
def test_campaign_total_and_plan_rows_fit(width,height):
    s=snapshot();s['progress'].update(campaign=True,campaign_plan_number=2,
        query_memory_gib_base=1,query_memory_gib_max=4,reserved_query_gib=32,active_dense_queries=1,dense_query_attempts=7)
    console=Console(file=io.StringIO(),width=width,height=height,no_color=True)
    with console.capture() as captured:console.print(render(s,width,height,page=0))
    text=captured.get();assert 'current plan 2' in text
    cpu=text.split('I/O WORKERS')[0]
    assert '5 done' in cpu and '5/20' not in cpu
    assert 'Query RAM 1-4 GiB/request' in text
    assert len(text.splitlines())<=height


def test_continuation_observer_counts_full_month_sessions(tmp_path):
    reporter=WorkerTelemetry(tmp_path,'test','cpu',0,heartbeat=False)
    reporter.begin(dict(ticker='A',month='2025-01',first_us=1,last_us=100))
    class Reader:
        plan={'continuation':{'parents':['earlier']}}
        def months(self,job):return [('2025-01',[dict(source_date='2025-01-02',last_sip_timestamp_us=10),dict(source_date='2025-01-03',last_sip_timestamp_us=100)])]
    observer=ObservedReader(Reader(),reporter,17)
    assert len(observer.months({})[0][1])==2
    assert reporter.snapshot()['progress_total']==2
    reporter.close()


def test_campaign_observer_reads_only_active_sibling_worker_root(tmp_path):
    campaign=tmp_path/'campaign';worker=tmp_path/'plan2';campaign.mkdir();(worker/'worker-status/new').mkdir(parents=True)
    (campaign/'execution.json').write_text(json.dumps(dict(worker_root='plan2',telemetry_run='new',workers=1,io_workers=0)))
    (campaign/'progress.json').write_text(json.dumps(dict(updated=time.time(),state='running')))
    (worker/'worker-status/new/cpu-00.json').write_text(json.dumps(dict(run='new',role='cpu',slot=0,pid=999999,created=1,state='Ready')))
    s=SnapshotReader(campaign).read();assert ('cpu',0) in s['workers']
    (campaign/'execution.json').write_text(json.dumps(dict(worker_root='../outside',workers=0,io_workers=0)))
    assert 'Invalid campaign worker root' in SnapshotReader(campaign).read()['read_errors']


def test_observer_exact_return_identity_and_optional_failure(tmp_path, monkeypatch):
    reporter = WorkerTelemetry(tmp_path, "run-1", "cpu", 0, heartbeat=False)
    reporter.begin(dict(ticker="A", month="2025-01"))
    result = ([object()], dict(queries=1))

    class Reader:
        def fetch(self, *args, **kwargs):
            assert args == ("A", [dict(source_date="2025-01-02")])
            assert kwargs == dict(frames=(0, 1))
            return result

    observed = ObservedReader(Reader(), reporter, 1)
    assert not hasattr(observed, "origin_counts")
    assert observed.fetch("A", [dict(source_date="2025-01-02")], frames=(0, 1)) is result
    reporter.session()
    reporter.end()
    reporter.write()
    data = json.loads(reporter.path.read_text())
    assert (data["assigned"], data["completed"], data["tickers"], data["queries"]) == (1, 1, 1, 1)
    from research.bar_gpt.v5.phase2_ar_shards import telemetry
    monkeypatch.setattr(telemetry, "write_json", lambda *a: (_ for _ in ()).throw(OSError("busy")))
    reporter.write()
    assert reporter.snapshot()["write_errors"] == 1
    reporter.close()


def test_reader_ignores_foreign_worker_and_retains_last_snapshot(tmp_path):
    (tmp_path / "execution.json").write_text(json.dumps(dict(telemetry_run="run-1", workers=1, io_workers=0)))
    (tmp_path / "progress.json").write_text(json.dumps(dict(updated=time.time(), bytes=100)))
    folder = tmp_path / "worker-status/run-1"
    folder.mkdir(parents=True)
    (folder / "cpu-00.json").write_text(json.dumps(dict(run="other", role="cpu", slot=0)))
    reader = SnapshotReader(tmp_path)
    assert not reader.read()["workers"]
    (tmp_path / "progress.json").write_text("partial")
    result = reader.read()
    assert result["progress"]["bytes"] == 100 and result["read_errors"]


def test_publication_observer_cannot_change_payload_or_seal(tmp_path):
    from test_bargpt_phase2_ar_sampled import Reader, compile, Geometry
    from research.bar_gpt.v5.phase2_ar_shards.storage import publish
    reader = Reader(days=("2025-11-03",), counts=(5000,), prices=(10,))
    session, compiled, _ = next(compile(reader))
    kwargs = dict(ticker="TEST", month=session["session"], liquid=1,
                  binding="binding", source_hash="source", compiled=compiled,
                  geometry=Geometry())
    events = []
    plain = publish(tmp_path / "plain", **kwargs)
    observed = publish(tmp_path / "observed", observer=lambda **kw: events.append(kw), **kwargs)
    broken = publish(tmp_path / "broken", observer=lambda **kw: (_ for _ in ()).throw(RuntimeError("observer only")), **kwargs)
    assert plain == observed == broken
    assert events[-1]["state"] == "Published"
    assert events[-1]["progress_done"] == events[-1]["progress_total"]
    for item in plain["files"]:
        paths = [next((tmp_path / root).rglob(item["name"])) for root in ("plain", "observed", "broken")]
        assert paths[0].read_bytes() == paths[1].read_bytes() == paths[2].read_bytes()


def test_observability_revision_requires_specific_parity_evidence(tmp_path):
    from research.bar_gpt.v5.phase2_ar_shards.source import verify_engineering_revision
    from research.bar_gpt.v5.shards.contract import digest, file_hash
    prior = dict(hash="plan", code={"phase2_ar_shards/run.py": "old"})
    current = dict(code={"phase2_ar_shards/run.py": "new", "phase2_ar_shards/telemetry.py": "new"})
    audit = dict(passed=True, plan_hash="plan", to_code=current["code"],
                 counts_parity_cases=12, complete_output_parity_cases=2)
    audit["hash"] = digest(audit)
    ap = tmp_path / "audit.json"
    ap.write_text(json.dumps(audit))
    proof = dict(kind="file-ledger-observability-v1", plan_hash="plan",
                 from_code=prior["code"], to_code=current["code"],
                 audit_path=str(ap), audit_sha256=file_hash(ap))
    proof["hash"] = digest(proof)
    pp = tmp_path / "proof.json"
    pp.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="observability lacks"):
        verify_engineering_revision(prior, current, pp, tmp_path)

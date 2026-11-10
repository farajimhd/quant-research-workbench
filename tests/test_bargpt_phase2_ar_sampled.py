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


@pytest.mark.parametrize("invalid", [None, "hash", "rows", "saturated", "pages"])
def test_empty_action_receipts_require_complete_provider_evidence(monkeypatch, invalid):
    import hashlib
    from research.bar_gpt.v5.phase2_ar_shards import inventory

    details = dict(sha256=hashlib.sha256(b"[]").hexdigest(), pages=1, saturated=False)
    proof = dict(source_run_id="empty-day", status="covered_empty",
                 coverage_start_date="2026-07-25", coverage_end_date="2026-07-26",
                 rows_read=0, rows_written=0, rows_failed=0)
    if invalid == "hash": details["sha256"] = "f" * 64
    if invalid == "rows": proof.update(rows_read=1, rows_written=1)
    if invalid == "saturated": details["saturated"] = True
    if invalid == "pages": details["pages"] = 0
    def rows(client, sql):
        if "publication_coverage" in sql:
            if "ORDER BY finished_at_utc" in sql:
                return []
            assert "'covered_empty'" in sql
            return [dict(proof, details_json=json.dumps(details))]
        return []
    monkeypatch.setattr(inventory.sip, "rows", rows)
    original = dict(coverage=dict(coverage_end_date="2026-07-25"), rows=[], catalog_hash=digest([]))
    if invalid:
        with pytest.raises(ValueError, match="coverage gap"):
            inventory.extend_actions(None, original, "2026-07-26")
    else:
        result = inventory.extend_actions(None, original, "2026-07-26")
        assert result["rows"] == [] and len(result["extension_coverage"]) == 1


def test_validation_reference_rebind_keeps_training_actions_and_records_revision(monkeypatch):
    from research.bar_gpt.v5.phase2_ar_shards import inventory

    past = dict(stock_split_id="old", execution_date="2025-02-03", provider_ticker="AAA", split_from=10, split_to=1)
    before = dict(past, stock_split_id="revised", execution_date="2026-03-26")
    after = dict(before, execution_date="2026-07-24")
    proof = dict(source_run_id="new2026", status="completed", coverage_start_date="2026-01-01", coverage_end_date="2026-10-03", rows_read=1, rows_written=1, rows_failed=0, details_json=json.dumps(dict(pages=1,saturated=False,sha256="a"*64)))
    monkeypatch.setattr(inventory.sip, "rows", lambda c, sql: [proof] if "publication_coverage" in sql else [after])
    original = dict(coverage=dict(coverage_end_date="2026-06-19"),rows=[past,before],catalog_hash=digest([past,before]))
    result = inventory.extend_actions(None, original, "2026-10-03")
    assert past in result["rows"] and before not in result["rows"] and after in result["rows"]
    assert result["frozen_training_rows_hash"] == digest([past])
    assert result["validation_revision"]["revised"] == [dict(before=before,after=after)]
    after["stock_split_id"] = "old"
    with pytest.raises(ValueError, match="frozen training"):
        inventory.extend_actions(None, original, "2026-10-03")


def test_parent_action_validation_uses_immutable_catalog_and_exact_receipt(monkeypatch):
    from research.bar_gpt.v5.phase2_ar_shards import inventory

    rows = [dict(stock_split_id="one", split_from=10, split_to=1)]
    receipt = dict(source_run_id="parent", coverage_start_date="2019-01-01", coverage_end_date="2026-06-19", rows_read=1, rows_written=1, rows_failed=0)
    original = dict(rows=rows, catalog_hash=digest(rows), coverage=dict(receipt, provider_response_sha256="a"*64))
    response = dict(receipt,details_json=json.dumps(dict(sha256="a"*64,saturated=False,pages=1)))
    def query(c, sql):
        assert "publication_coverage" in sql and "stock_split_v1" not in sql
        return [response]
    monkeypatch.setattr(inventory.sip,"rows",query)
    inventory.verify_parent_actions(None,original)
    response["rows_written"] = 0
    with pytest.raises(ValueError,match="acquisition receipt"):
        inventory.verify_parent_actions(None,original)
    rows[0]["split_from"] = 20
    with pytest.raises(ValueError,match="catalog changed"):
        inventory.verify_parent_actions(None,original)


def test_dense_pages_preserve_whole_sessions_and_resource_retry_order(monkeypatch):
    from contextlib import contextmanager
    from research.bar_gpt.v5.phase2_ar_shards import source

    reader = source.CanonicalReader.__new__(source.CanonicalReader)
    reader.plan, reader.client = dict(authority=dict(rules=[])), object()
    days = [dict(source_date=f"2024-01-{i:02d}", event_count=3_000_000) for i in range(1,5)]
    assert [len(p) for p in reader.pages(days)] == [2,2]
    assert len(list(reader.pages([dict(days[0], event_count=20_000_000)]))[0]) == 1
    monkeypatch.setattr(source,"bar_query",lambda ticker,page,rules,coarse:json.dumps(page))
    monkeypatch.setattr(source,"partition_page",lambda sql,page:sql)
    def arrow(client,sql,*args,**kwargs):
        page=json.loads(sql)
        if len(page)>1: raise RuntimeError("TIMEOUT_EXCEEDED")
        return pl.DataFrame(dict(resolution=[1_000_000],stamp=[int(page[0]["source_date"][-2:])]))
    monkeypatch.setattr(source.sip,"arrow",arrow)
    monkeypatch.setattr(source,"unpack",lambda table:(np.ones((len(table),18),np.float32),np.ones((len(table),18),np.uint8),table["stamp"].to_numpy()))
    active=[]
    @contextmanager
    def gate():
        assert not active
        active.append(1)
        try:yield
        finally:active.pop()
    arrays,timing=reader.fetch("X",days,gate,frames=(0,))
    assert arrays[0]["end_us"].tolist()==[1,2,3,4]
    assert timing["resource_page_splits"]==2 and timing["queries"]==6
    def corrupt(*args,**kwargs):raise RuntimeError("bad source schema")
    monkeypatch.setattr(source.sip,"arrow",corrupt)
    with pytest.raises(RuntimeError,match="bad source schema"):
        reader.fetch("X",days,gate,frames=(0,))


def test_raw_coarse_cache_binds_source_rows_and_detects_corruption(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from research.bar_gpt.v5.phase2_ar_shards.coarse_cache import CoarseCache
    from research.bar_gpt.v5.phase2_ar_shards import coarse_cache

    monkeypatch.setattr(coarse_cache.psutil,"disk_usage",lambda _:SimpleNamespace(free=200*1024**3))
    cache=CoarseCache(tmp_path,"authority")
    table=pl.DataFrame(dict(resolution=[3_600_000_000],price=[np.float32(1.25)],end_us=[10]))
    calls=[]
    def fetch():calls.append(1);return table
    first,hit=cache.get("SELECT one","source-rows",fetch)
    second,again=cache.get("SELECT one","source-rows",fetch)
    assert not hit and again and first.equals(second) and len(calls)==1
    cache.get("SELECT one","different-ordinal-proof",fetch)
    assert len(calls)==2
    marker=next(tmp_path.glob('*/*/receipt.json'));receipt=json.loads(marker.read_text())
    assert receipt['identity']['price_basis']=='unadjusted'
    payload=marker.parent/'bars.arrow';payload.write_bytes(payload.read_bytes()+b'bad')
    with pytest.raises(ValueError,match="cache changed"):
        cache.get(receipt['identity']['sql'],receipt['identity']['source_rows'],fetch)


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


def research_fixture(tmp_path,monkeypatch):
    from research.bar_gpt.v5.phase2_ar_shards import inspection
    from research.bar_gpt.v5.phase2_ar_shards.contract import VERSION
    from research.bar_gpt.v5.shards.contract import write_json

    reader=Reader(days=("2026-01-02",),counts=(5000,),prices=(10,))
    session,compiled,_=next(compile(reader))
    plan=dict(version=VERSION,jobs=[dict(ticker="TEST",years=[dict(year=2026,liquid=1)])],authority=dict(corporate_actions=dict(rows=[])))
    plan['hash']=digest(plan);write_json(tmp_path/'plan.json',plan)
    write_json(tmp_path/'provenance.json',dict(source_revision='test'))
    write_json(tmp_path/'progress.json',dict(state='running',bytes=1,units=1,updated=1))
    receipt=publish(tmp_path,'TEST',session['session'],1,plan['hash'],digest(session),compiled,Geometry())
    directory=tmp_path/'shards'/digest('TEST')[:24]/session['session']
    cat=Catalog(tmp_path);cat.register(directory,receipt);cat.inspection_catalog(plan);cat.db.close()
    monkeypatch.setattr(inspection,'ROOT',tmp_path)
    entry=inspection.catalog()['streams'][0]
    return inspection,entry,directory,receipt,compiled


def test_research_adapter_uses4096_origins_and_all_five_causal_views(tmp_path,monkeypatch):
    inspection,entry,directory,receipt,compiled=research_fixture(tmp_path,monkeypatch)
    assert entry['blocks']==len(compiled['blocks'])
    assert inspection.units(entry['stream'])['units'][0]['unit']==20260102
    for frame in range(5):
        sample=inspection.sample(stream=entry['stream'],frame=frame)
        assert len(sample['decisions'])==4096 and len(sample['bars'])==64
        assert len(sample['channels'])==18 and len(sample['time_channels'])==9
        assert all(b['end_us']<=sample['origin_us'] for b in sample['bars'])
        if frame==0:
            assert sample['bars'][-1]['end_us']==sample['origin_us']
    with pytest.raises(ValueError):inspection.sample(stream='../bad')
    with pytest.raises(ValueError):inspection.sample(stream=entry['stream'],decision=4096)
    payload=directory/receipt['files'][0]['name'];payload.write_bytes(payload.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='mutation'):
        inspection.sample(stream=entry['stream'])


def test_research_api_accepts4095_and_rejects4096(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.backend.research_model_service import router

    inspection,entry,_,_,_=research_fixture(tmp_path,monkeypatch)
    app=FastAPI();app.include_router(router)
    with TestClient(app) as client:
        response=client.get('/api/research/models/bargpt/phase2/sample',params=dict(stream=entry['stream'],decision=4095,frame=4))
        assert response.status_code==200 and response.json()['decision']==4095
        assert client.get('/api/research/models/bargpt/phase2/sample',params=dict(stream=entry['stream'],decision=4096)).status_code==422


def test_explicit_pilot_units_cannot_silently_change_full_build(tmp_path):
    from research.bar_gpt.v5.phase2_ar_shards.run import ordered_units
    pl.DataFrame([dict(ticker='X',month='2025-08',split=0),dict(ticker='X',month='2026-01',split=1)]).write_parquet(tmp_path/'units.parquet')
    plan=dict(sample_units=dict(name='units.parquet'))
    assert len(ordered_units(tmp_path,plan))==2
    assert [r['month'] for r in ordered_units(tmp_path,plan,pilot_units=['X:2025-08'])]==['2025-08']
    with pytest.raises(ValueError,match='Unknown frozen'):
        ordered_units(tmp_path,plan,pilot_units=['X:2025-07'])
    with pytest.raises(ValueError,match='resource configuration'):
        main(['--source-revision','test','--pilot-unit','X:2025-08'])


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
        jobs=[dict(ticker="TEST",years=[dict(year=2026,liquid=1)])],
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

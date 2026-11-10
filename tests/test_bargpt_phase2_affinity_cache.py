"""Ticker ownership and exact persistent-history reuse, without market queries."""

from contextlib import nullcontext
import json
import io

import numpy as np
import pytest
from rich.console import Console

from research.bar_gpt.v5.phase2_ar_shards.assignment import assign
from research.bar_gpt.v5.phase2_ar_shards.bar_cache import BarCache
from research.bar_gpt.v5.phase2_ar_shards.contract import BAR
from research.bar_gpt.v5.phase2_ar_shards.source import CanonicalReader
from research.bar_gpt.v5.shards.sql import clock


def fixtures():
    from test_bargpt_phase2_ar_sampled import Reader
    reader = Reader(days=("2025-11-03", "2025-11-04"), counts=(5000, 5000), prices=(10, 12))
    rows = [r for _, month in reader.months({}) for r in month if r["source_date"] in reader.data]
    return reader, rows


def test_assign_whole_tickers_chronologically_and_deterministically():
    units = [dict(ticker=t, month=f"2025-{m:02d}", year=2025, split=0, marker=t+str(m))
             for m in (3, 1, 2) for t in ("A", "B", "C", "D")]
    jobs = [dict(ticker=t, years=[dict(year=2025, events=100*(i+1))])
            for i, t in enumerate(("A", "B", "C", "D"))]
    queues, manifest = assign(units, jobs, 3)
    assert manifest == assign(units, jobs, 3)[1]
    flattened = [u for q in queues for u in q]
    assert sorted(flattened, key=lambda u:u["marker"]) == sorted(units, key=lambda u:u["marker"])
    owners = {}
    for slot, queue in enumerate(queues):
        for u in queue:
            assert owners.setdefault(u["ticker"], slot) == slot
        for ticker in {u["ticker"] for u in queue}:
            assert [u["month"] for u in queue if u["ticker"] == ticker] == ["2025-01", "2025-02", "2025-03"]
    with pytest.raises(ValueError, match="Duplicate"):
        assign(units + [units[0]], jobs, 3)


def test_persistent_context_reload_is_exact_and_unadjusted(tmp_path):
    reader, rows = fixtures()
    arrays, _ = reader.fetch("TEST", rows, nullcontext)
    cache = BarCache(tmp_path, "source")
    for row in rows:
        cache.put("TEST", row, False, arrays)
        cache.put("TEST", row, True, arrays)
    fresh = BarCache(tmp_path, "source")
    for row in rows:
        for coarse in (False, True):
            actual, kind = fresh.get("TEST", row, coarse)
            assert kind == "disk"
            for i in range(5):
                a = arrays[i]
                expected = a[(a["end_us"] > clock(row["source_date"],4)) &
                             (a["end_us"] <= clock(row["source_date"],20))]
                expected = expected if coarse and i >= 3 else expected[-64:] if not coarse and i < 3 else np.empty(0,dtype=BAR)
                assert actual[i].tobytes() == expected.tobytes()
            assert fresh.get("TEST", row, coarse)[1] == "memory"
    assert BarCache(tmp_path,"another-source").get("TEST",rows[0],False) == (None,None)
    changed = dict(rows[0], event_count=123456)
    assert fresh.get("TEST",changed,False) == (None,None)


def test_changed_persistent_context_fails_closed(tmp_path):
    reader, rows = fixtures()
    arrays, _ = reader.fetch("TEST", rows, nullcontext)
    cache = BarCache(tmp_path,"source")
    cache.put("TEST",rows[0],False,arrays)
    payload = next(tmp_path.rglob("*.npz"))
    payload.write_bytes(payload.read_bytes()+b"changed")
    with pytest.raises(ValueError,match="context changed"):
        BarCache(tmp_path,"source").get("TEST",rows[0],False)


def test_history_reads_use_ram_then_disk_without_source_queries(tmp_path):
    baseline, rows = fixtures()
    reader = CanonicalReader.__new__(CanonicalReader)
    reader.context_cache = BarCache(tmp_path,"source")
    reader.pages = baseline.pages
    calls = []
    def source(*args, **kwargs):
        calls.append((args, kwargs))
        return baseline.fetch(*args, **kwargs)
    reader._fetch_uncached = source
    full, first = reader.fetch("TEST",rows,nullcontext)
    assert first["queries"] == 2
    count = len(calls)
    history, second = reader.history_fetch("TEST",rows,nullcontext)
    assert second["queries"] == 0 and len(calls) == count
    for a, b in zip(history, full):
        assert a[-64:].tobytes() == b[-64:].tobytes()
    reader.context_cache = BarCache(tmp_path,"source")
    disk, third = reader.history_fetch("TEST",rows,nullcontext)
    assert third["context_disk_hits"] == 4 and third["queries"] == 0
    assert all(a.tobytes()==b.tobytes() for a,b in zip(history,disk))
    # A tail is NEVER accepted as the complete fine-frame current session.
    again, final = reader.fetch("TEST",rows,nullcontext)
    assert final["queries"] == 1 and all(a.tobytes()==b.tobytes() for a,b in zip(full,again))


def test_primary_worker_bar_does_not_reset_between_months():
    from test_bargpt_phase2_progress_panel import snapshot
    from research.bar_gpt.v5.phase2_progress_panel import render
    s = snapshot()
    s["progress"]["worker_assignments"] = [dict(slot=i,total=100,completed=25,deferred=5,tickers=4) for i in range(32)]
    s["workers"]["cpu",0].update(progress_done=0,progress_total=20)
    console = Console(file=io.StringIO(), width=200, no_color=True)
    with console.capture() as capture:
        console.print(render(s,200,52,page=0))
    assert "30/100" in capture.get() and "fixed assigned months" in capture.get()


def test_cross_month_context_and_split_outputs_match_full_history(tmp_path):
    from datetime import date, timedelta
    from test_bargpt_phase2_ar_sampled import Reader
    from research.bar_gpt.v5.phase2_ar_shards.sampling import compile_sessions
    raw = Reader(days=("2025-10-31", "2025-11-03"), counts=(5000,5000), prices=(10,40))
    dates = [(date.fromisoformat(raw.prior)-timedelta(days=n)).isoformat() for n in range(64,-1,-1)] + list(raw.days)
    rows = [dict(source_date=d,event_count=10000,first_sip_timestamp_us=clock(d,4),last_sip_timestamp_us=clock(d,20)) for d in dates]
    all_bars = [np.concatenate([raw.history[i], *[raw.data[d][i] for d in raw.days]]) for i in range(5)]

    class FullReader:
        def months(self, job):return [("all", rows)]
        def pages(self, selected, chunk_events):
            yield from CanonicalReader.pages(self, selected, chunk_events)
        def fetch(self,ticker,selected,gate,chunk_events=0,frames=tuple(range(5))):
            masks = [np.logical_or.reduce([(a["end_us"]>clock(r["source_date"],4)) & (a["end_us"]<=clock(r["source_date"],20)) for r in selected]) for a in all_bars]
            return [a[m] if i in frames else np.empty(0,dtype=BAR) for i,(a,m) in enumerate(zip(all_bars,masks))],dict(queries=1,query_seconds=.01)

    baseline = FullReader()
    cached = CanonicalReader.__new__(CanonicalReader)
    cached.context_cache = BarCache(tmp_path,"source")
    cached.months = baseline.months
    cached.origin_counts = None
    cached._fetch_uncached = baseline.fetch
    actions = [dict(execution_date="2025-11-03",split_from=1,split_to=4)]
    for day in raw.days:
        unit = dict(ticker="TEST",month=day[:7],first_us=clock(day,4),last_us=clock(day,4),liquid=1,split=0)
        kwargs = dict(gate=nullcontext,actions=actions,chunk_events=2_000_000)
        old = list(compile_sessions(baseline,{},unit,**kwargs))
        new = list(compile_sessions(cached,{},unit,**kwargs))
        assert len(old) == len(new) == 1
        a,b = old[0][1],new[0][1]
        assert all(x.tobytes()==y.tobytes() for x,y in zip(a["views"],b["views"]))
        assert a["blocks"].tobytes()==b["blocks"].tobytes()
        assert a["pending"].tobytes()==b["pending"].tobytes()
        assert a["stats"]==b["stats"] and a["sampling"]==b["sampling"]

"""HOD producer skips sealed units and never covers uncertain child writes."""

from http.client import IncompleteRead

import pytest

from pipelines.strategy_one import hod_publication as subject
from src.trading_runtime.strategy_one_hod_product import HodContext


SCOPE = subject.HodPublicationScope(
    "build", "2026-08-18", "TEST",
    "00000000-0000-0000-0000-000000000001",
    "00000000-0000-0000-0000-000000000002",
    "a" * 64, "b" * 64, (300_100,))


def test_verified_unit_skips_full_100ms_derivation(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda *_args, **_kwargs: SCOPE)
    monkeypatch.setattr(subject, "_verify_existing",
                        lambda *_args: "00000000-0000-0000-0000-000000000003")
    monkeypatch.setattr(subject, "_derive", lambda *_args:
                        pytest.fail("sealed HOD unit was recalculated"))
    assert subject.publish_unit(None, None, None, None, None, None,
                                ticker="TEST") == "skipped"


def test_uncertain_child_readback_never_publishes_coverage(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda *_args, **_kwargs: SCOPE)
    monkeypatch.setattr(subject, "_verify_existing", lambda *_args: None)
    expected = (HodContext(300_100, 100_000, 120_000, True, "r11"),)
    monkeypatch.setattr(subject, "_derive", lambda *_args: expected)
    monkeypatch.setattr(subject, "_insert_children", lambda *_args: None)
    monkeypatch.setattr(subject, "_child", lambda *_args:
                        (HodContext(300_100, 100_000, 120_000, False, ""),))

    class Writer:
        def execute(self, _sql):
            pytest.fail("uncertain child attempted to publish coverage")

    with pytest.raises(subject.HodReadbackMismatch, match="read-back"):
        subject.publish_unit(Writer(), None, None, None, None, None,
                             ticker="TEST")


def test_truncated_source_restarts_entire_ticker_before_any_insert(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda *_args, **_kwargs: SCOPE)
    monkeypatch.setattr(subject, "_verify_existing", lambda *_args: None)
    calls = []
    def interrupted(*_args):
        calls.append("read")
        raise IncompleteRead(b"partial", 100)
    monkeypatch.setattr(subject, "_derive", interrupted)
    monkeypatch.setattr(subject, "_insert_children", lambda *_args:
                        pytest.fail("incomplete SELECT reached INSERT"))
    retried = []
    with pytest.raises(IncompleteRead):
        subject.publish_unit(None, None, None, None, None, None,
                             ticker="TEST",
                             on_source_retry=lambda: retried.append(True))
    assert calls == ["read"] * 3
    assert retried == [True, True]


def test_uncertain_insert_is_not_retried_as_a_source_read(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda *_args, **_kwargs: SCOPE)
    monkeypatch.setattr(subject, "_verify_existing", lambda *_args: None)
    derived = []
    expected = (HodContext(300_100, 100_000, 120_000, True, "r11"),)
    def read(*_args):
        derived.append(True)
        if len(derived) == 1:
            raise IncompleteRead(b"partial", 100)
        return expected
    monkeypatch.setattr(subject, "_derive", read)
    def uncertain_insert(*_args):
        raise RuntimeError("insert uncertainty")
    monkeypatch.setattr(subject, "_insert_children", uncertain_insert)
    retried = []
    with pytest.raises(RuntimeError, match="insert uncertainty"):
        subject.publish_unit(None, None, None, None, None, None,
                             ticker="TEST",
                             on_source_retry=lambda: retried.append(True))
    assert len(derived) == 2
    assert retried == [True]


def test_100ms_source_is_bounded_by_nonoverlapping_pinned_bucket_ranges():
    scope = subject.HodPublicationScope(
        SCOPE.build_id, SCOPE.session_date, SCOPE.ticker,
        SCOPE.bars_attempt_id, SCOPE.candidate_attempt_id,
        SCOPE.candidate_content_hash, SCOPE.seed_plan_token,
        (2_100_000,))
    class Reader:
        def __init__(self):
            self.queries = []
        def iter_json_each_row(self, sql):
            self.queries.append(sql)
            yield {"bucket_index": 144_000 + (len(self.queries) - 1) * 10_000}
    reader = Reader()
    rows = list(subject._bars_100ms(reader, scope))
    assert [row["bucket_index"] for row in rows] == [144_000, 154_000, 164_000]
    assert len(reader.queries) == 3
    for sql, start, end in zip(reader.queries,
                               (144_000, 154_000, 164_000),
                               (154_000, 164_000, 165_000), strict=True):
        assert sql.lstrip().startswith("SELECT")
        assert f"bucket_index>={start} AND bucket_index<{end}" in sql
        assert "attempt_id=toUUID" in sql
        assert "ORDER BY bucket_index FORMAT JSONEachRow" in sql


def test_second_chunks_close_before_cpu_consumer_receives_rows(monkeypatch):
    calls = []
    closed = []
    def source(_market, **kwargs):
        calls.append((kwargs["after_boundary_ms"], kwargs["through_boundary_ms"]))
        try:
            yield {"boundary": kwargs["through_boundary_ms"]}
        finally:
            closed.append(kwargs["through_boundary_ms"])
    monkeypatch.setattr(subject, "iter_persisted_v7_seconds", source)
    stream = subject._seconds_1s(None, None, SCOPE, 2_001_000)
    assert next(stream) == {"boundary": 1_000_000}
    assert closed == [1_000_000]
    assert list(stream) == [{"boundary": 2_000_000}, {"boundary": 2_001_000}]
    assert calls == [(0, 1_000_000), (1_000_000, 2_000_000), (2_000_000, 2_001_000)]


def test_incomplete_second_chunk_does_not_emit_partial_rows(monkeypatch):
    def source(*_args, **_kwargs):
        yield {"partial": True}
        raise IncompleteRead(b"partial", 100)
    monkeypatch.setattr(subject, "iter_persisted_v7_seconds", source)
    stream = subject._seconds_1s(None, None, SCOPE, 1_000)
    with pytest.raises(IncompleteRead):
        next(stream)

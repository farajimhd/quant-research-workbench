"""V7 derivative child attempts stay invisible until exact coverage read-back."""
from dataclasses import replace
from uuid import UUID

import pytest

from pipelines.strategy_one import v7_interval_publication as publication
from pipelines.strategy_one.v7_interval_derivation import DerivedV7TickerDay
from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval


ATTEMPT = str(UUID(int=21))


class Writer:
    def __init__(self):
        self.sqls = []

    def execute(self, sql):
        self.sqls.append(sql)
        return ""


def item():
    return DerivedV7TickerDay(
        "build-1", "2026-08-18", "TEST", str(UUID(int=1)),
        "d" * 64, "a" * 64, "b" * 64, "c" * 64,
        "legacy-unfiltered", (1_000, 2_000),
        (V7LevelInterval("Z", 0, 0, 57_600_001, 10.0, 10.2,
                         "resistance", "", 1_800_000_000_000, True),
         V7LevelInterval("A", 1, 0, 57_600_001, 11.0, 11.2,
                         "support", "", 1_800_000_000_000, True)))


def test_v7_coverage_is_last_after_bounded_exact_child_readback(monkeypatch):
    expected = item()
    writer = Writer()
    monkeypatch.setattr(publication, "verify_tables", lambda _client: None)
    def clocks(_client, _item, _attempt):
        return (expected.valid_seconds if any("INSERT INTO arte.strategy_one_v7_clock_v1"
                                              in sql for sql in writer.sqls) else ())
    def intervals(_client, _item, _attempt):
        return (publication._expected_intervals(expected)
                if any("INSERT INTO arte.strategy_one_v7_level_interval_v1"
                       in sql for sql in writer.sqls) else ())
    def coverage(_client, _item):
        return ([{"derivation_attempt_id": ATTEMPT,
                  **publication._expected_coverage(expected)}]
                if any("INSERT INTO arte.strategy_one_v7_coverage_v1"
                       in sql for sql in writer.sqls) else [])
    monkeypatch.setattr(publication, "_read_clocks", clocks)
    monkeypatch.setattr(publication, "_read_intervals", intervals)
    monkeypatch.setattr(publication, "_coverage", coverage)
    assert publication.publish_unit(writer, writer, expected,
                                    clock_batch_size=1,
                                    attempt_id=ATTEMPT) == "published"
    assert ["clock" if "INSERT INTO arte.strategy_one_v7_clock_v1" in sql
            else "interval" if "INSERT INTO arte.strategy_one_v7_level_interval_v1" in sql
            else "coverage" for sql in writer.sqls] == [
                "clock", "clock", "interval", "interval", "coverage"]
    assert publication.publish_unit(writer, writer, expected,
                                    attempt_id=str(UUID(int=22))) == "already_published"
    assert len(writer.sqls) == 5


def test_v7_uncertain_child_never_reaches_coverage(monkeypatch):
    expected = item()
    writer = Writer()
    monkeypatch.setattr(publication, "verify_tables", lambda _client: None)
    monkeypatch.setattr(publication, "_coverage", lambda *_args: [])
    monkeypatch.setattr(publication, "_read_clocks",
                        lambda *_args: expected.valid_seconds
                        if writer.sqls else ())
    monkeypatch.setattr(publication, "_read_intervals", lambda *_args: ())
    with pytest.raises(RuntimeError, match="child read-back"):
        publication.publish_unit(writer, writer, expected,
                                 attempt_id=ATTEMPT)
    assert all("INSERT INTO arte.strategy_one_v7_coverage_v1" not in sql
               for sql in writer.sqls)


def test_v7_malformed_intervals_fail_before_any_insert():
    expected = item()
    writer = Writer()
    duplicate = replace(expected.intervals[0], valid_from_ms=1_000)
    malformed = replace(expected, intervals=(*expected.intervals, duplicate))
    with pytest.raises(ValueError, match="overlap"):
        publication.publish_unit(writer, writer, malformed)
    assert writer.sqls == []

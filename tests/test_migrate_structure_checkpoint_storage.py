import pytest

from scripts.migrate_structure_checkpoint_storage import (
    ClickHouse, CURRENT_SET, REQUIRED_SETS, identifier, literal, retention,
)


def test_retention_preserves_recovery_dependencies_and_live_contracts():
    predicate = retention("qmd_structure_daily_checkpoint_v2", list(REQUIRED_SETS))
    assert all(value in predicate for value in REQUIRED_SETS)
    assert CURRENT_SET in predicate
    assert "validation" not in predicate
    assert retention("qmd_structure_daily_checkpoint_v1", []) == "1"
    assert retention("qmd_structure_state_v2", []) == "1"


def test_sql_identifiers_and_literals_cannot_expand_cleanup_scope():
    for name in ("q_live; DROP DATABASE q_live", "x.y", "x`", ""):
        with pytest.raises(ValueError):
            identifier(name)
    assert identifier("q_live") == "`q_live`"
    assert literal("x'y\\z") == "'x\\'y\\\\z'"


def test_digest_stream_rejects_late_clickhouse_errors_and_preserves_duplicates():
    class Response:
        lines = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_lines(self): return iter(self.lines)

    response = Response()
    ch = object.__new__(ClickHouse)
    ch.request = lambda *args, **kwargs: response
    response.lines = [b"A" * 64, b"A" * 64]
    two = ch.digest("SELECT")
    response.lines = [b"A" * 64]
    one = ch.digest("SELECT")
    assert two["rows"] == 2 and one["rows"] == 1
    assert two["sha256"] != one["sha256"]
    response.lines.append(b"Code: 241. Memory limit exceeded")
    with pytest.raises(RuntimeError, match="Invalid digest stream"):
        ch.digest("SELECT")

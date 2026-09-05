import pytest
from unittest.mock import Mock

from scripts.migrate_structure_checkpoint_storage import (
    ClickHouse, CURRENT_SET, REQUIRED_SETS, identifier, literal, retention, table_digest,
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


def test_checkpoint_queries_bound_serialization_blocks_without_raising_memory_limit():
    ch = object.__new__(ClickHouse)
    ch.url = "http://test"
    ch.session = Mock()
    ch.session.post.return_value.status_code = 200
    ch.request("SELECT snapshot_json", stream=True)
    settings = ch.session.post.call_args.kwargs["params"]
    assert settings["max_block_size"] == 16
    assert settings["preferred_block_size_bytes"] == 1048576
    assert settings["max_threads"] == 1
    assert settings["max_memory_usage"] == 4294967296


def test_full_table_digest_covers_partition_identity_rows_and_content():
    ch = Mock()
    ch.rows.return_value = [{"p": "202512"}, {"p": "202511"}]
    ch.digest.return_value = {"rows": 2, "sha256": "a" * 64}
    original = table_digest(ch, "source", "snapshot_json")
    assert original["rows"] == 4
    assert "_partition_id='202511'" in ch.digest.call_args_list[0].args[0]
    ch.rows.return_value.reverse()
    assert table_digest(ch, "target", "snapshot_json") == original
    ch.rows.return_value.append({"p": "202601"})
    assert table_digest(ch, "target", "snapshot_json") != original
    ch.rows.return_value.pop()
    ch.digest.return_value = {"rows": 2, "sha256": "b" * 64}
    assert table_digest(ch, "target", "snapshot_json") != original

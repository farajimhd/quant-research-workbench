"""Bounded read-only source-table queries used by the registered worker."""
import json
import re

import pytest

from src.backend.signal_source_clickhouse_storage import ClickHouseSignalSourceReadStorage
from src.backend.signal_stream_typed_occurrence import project_typed_occurrence
from tests.test_signal_stream_typed_publication import _batch


class _Client:
    def __init__(self, families=None):
        self.families = families or {}
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        table = re.search(r"FROM arte\.([a-z0-9_]+)", sql).group(1)
        return "\n".join(json.dumps(row) for row in self.families.get(table, ()))


def test_source_reader_projects_exact_occurrence_without_writes():
    batch = _batch()
    occurrence = batch.occurrences[0]
    source = batch.catalogs[occurrence["signal_stream_id"]]
    projected = project_typed_occurrence(
        occurrence, source.catalog, source.stream, source.columns)
    client = _Client({
        "signal_stream_python_occurrence_v1": [projected["parent"]],
        "signal_stream_python_rule_v1": projected["rules"],
        "signal_stream_python_column_evidence_v1": projected["columns"],
        "signal_stream_python_field_evidence_v1": projected["fields"],
    })
    storage = ClickHouseSignalSourceReadStorage(client, batch.catalogs)
    assert storage.read_exact_prior_occurrence(occurrence["event_id"]) == occurrence
    assert client.sql
    assert all(sql.startswith("SELECT ") and " FORMAT JSONEachRow" in sql
               for sql in client.sql)
    assert all("LIMIT 65537" in sql for sql in client.sql)


def test_source_reader_rejects_unbounded_or_foreign_scope():
    storage = ClickHouseSignalSourceReadStorage(_Client(), {})
    with pytest.raises(ValueError, match="event identity"):
        storage.read_occurrence_rows("signal_stream_python_occurrence_v1",
                                     event_id="unsafe'event")
    with pytest.raises(ValueError, match="cursor scope"):
        storage.read_cursor_rows("signal_stream_cursor_commit_typed_v1",
                                 session_key="2026-09-24", batch_sequence=0)
    with pytest.raises(ValueError, match="table is invalid"):
        storage.read_occurrence_rows("trading_event_v1", event_id="a" * 64)


def test_source_reader_checks_every_family_before_new_gate():
    client = _Client()
    storage = ClickHouseSignalSourceReadStorage(client, {})
    assert storage.has_any_source_rows(session_key="2026-09-24") is False
    assert len(client.sql) == 8
    assert all(sql.startswith("SELECT 1 FROM arte.signal_stream_")
               for sql in client.sql)

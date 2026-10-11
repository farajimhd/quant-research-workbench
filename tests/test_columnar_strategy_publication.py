import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from test_columnar_strategy_release import Reader, bundle
from src.trading_runtime.columnar_strategy_release import TABLES, project_release
from src.trading_runtime.columnar_strategy_schema import FIELD, RELEASE
from src.trading_runtime.columnar_strategy_publication import publish_program_release


class Client(Reader):
    base_url = "http://test"
    def __init__(self, rows, *, writer=False, fail=None):
        super().__init__(rows)
        self.writer, self.fail, self.writes = writer, fail, []
    def execute(self, query):
        if query == "SHOW GRANTS FINAL" and self.writer:
            return super().execute(query) + "\n" + "\n".join(
                f"GRANT INSERT ON arte.{table.name} TO program_release_reader" for table in TABLES)
        if query.startswith("INSERT"):
            assert self.writer
            name = re.search(r"`arte`\.`([a-z0-9_]+)`", query).group(1)
            self.writes.append(name)
            self.rows[name] = tuple(json.loads(line) for line in query.splitlines()[1:])
            if name == self.fail:
                raise RuntimeError("ambiguous INSERT")
            return ""
        return super().execute(query)


class Keeper:
    connected = True
    client_state = SimpleNamespace(name="CONNECTED")
    def __init__(self):
        self.claims = {}
        self.creation_id = 1
    def ensure_path(self, path):
        pass
    def create(self, path, value, *, ephemeral):
        assert not ephemeral
        if path in self.claims:
            raise RuntimeError("claim exists")
        self.claims[path] = (value, 0)
    def get(self, path):
        value, version = self.claims[path]
        return value, SimpleNamespace(version=version, czxid=self.creation_id)
    def set(self, path, value, *, version):
        assert self.claims[path][1] == version
        self.claims[path] = value, version + 1


def setup():
    spec, fields, kwargs = bundle()
    rows = project_release(spec, fields, **kwargs)
    stored = {table.name: () for table in TABLES}
    keeper = Keeper()
    return rows, stored, keeper


def publish(rows, stored, keeper, *, fail=None):
    reader, writer = Client(stored), Client(stored, writer=True, fail=fail)
    try:
        result = asyncio.run(publish_program_release(lambda: reader, lambda: writer, keeper, rows))
        return result, writer.writes
    finally:
        assert reader.closed and writer.closed


def test_concrete_transport_seals_last_commits_fence_and_exact_retry_only_reads():
    rows, stored, keeper = setup()
    result, writes = publish(rows, stored, keeper)
    assert writes == [table.name for table in TABLES]
    assert writes[-1] == RELEASE.name
    assert next(iter(keeper.claims.values()))[0].startswith(b"1\ncommitted\n")
    again, writes = publish(rows, stored, keeper)
    assert again.header == result.header and writes == []


def test_ambiguous_insert_leaves_claim_and_children_unsealed_never_automatically_adopted():
    rows, stored, keeper = setup()
    with pytest.raises(RuntimeError, match="ambiguous INSERT"):
        publish(rows, stored, keeper, fail=FIELD.name)
    assert stored[FIELD.name] and not stored[RELEASE.name]
    with pytest.raises(RuntimeError, match="reconcile explicitly"):
        publish(rows, stored, keeper)
    assert not stored[RELEASE.name]


def test_ownership_and_storage_fail_before_writes():
    rows, stored, keeper = setup()
    keeper.connected = False
    with pytest.raises(RuntimeError, match="unavailable"):
        publish(rows, stored, keeper)
    assert not any(stored.values())
    keeper.connected = True
    reader, writer = Client(stored), Client(stored, writer=True)
    reader.bad_disk = True
    with pytest.raises(ValueError, match="outside live_market_ssd"):
        asyncio.run(publish_program_release(lambda: reader, lambda: writer, keeper, rows))
    assert not any(stored.values()) and not keeper.claims


def test_recreated_identical_claim_is_not_the_original_owner():
    rows, stored, keeper = setup()
    reader = Client(stored)
    class LosingWriter(Client):
        def execute(self, query):
            result = super().execute(query)
            if query.startswith("INSERT"):
                keeper.creation_id += 1  # Same bytes, different creation receipt.
            return result
    writer = LosingWriter(stored, writer=True)
    with pytest.raises(RuntimeError, match="ownership claim changed"):
        asyncio.run(publish_program_release(lambda: reader, lambda: writer, keeper, rows))
    assert stored[FIELD.name] and not stored[RELEASE.name]
    assert reader.closed and writer.closed

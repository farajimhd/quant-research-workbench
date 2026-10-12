from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import math
import json
import asyncio
from threading import Event

import pytest

from src.trading_runtime import arte_oms_observation_snapshot as module


def snapshot(fingerprint):
    return module.project_shared_oms_observations(run_id="shared-run",
        session_date=date(2026, 8, 18), checkpoint_sequence=42, boundary_ms=30000,
        groups={"group": SimpleNamespace(broker_order_ids=("broker",),
            broker_order_state_fingerprints={"broker": fingerprint})})


@pytest.mark.parametrize("kind", ["canonical", "live_adapter"])
def test_exact_native_fingerprint_survives_shared_codec(kind):
    numbers = (math.nextafter(0.0, 1.0), math.nextafter(5.0, 6.0),
               math.nextafter(0.001, 1.0), math.nextafter(10.0, 11.0), 0.0)
    fingerprint = (("working", "Submitted", *numbers, "", "", "")
        if kind == "canonical" else ("Submitted", *numbers, ""))
    rows = snapshot(fingerprint)
    assert rows == snapshot(fingerprint)
    codec = module.verified_oms_observation_codec_rows(rows)
    child = codec.observations[0]
    restored = tuple(float(child[name]) for name in module._NUMBERS)
    assert tuple(value.hex() for value in restored) == tuple(value.hex() for value in numbers)
    assert child["fingerprint_kind"] == kind
    assert all("strategy_one" not in table.name for table in module.TABLES)
    assert all(kind == "UInt64" for name, kind in module.OBSERVATION.columns
               if name.endswith("_bits"))
    with pytest.raises(TypeError):
        rows.root["boundary_ms"] = 40000
    with pytest.raises(TypeError):
        rows.observations[0]["warning"] = "changed"


def test_missing_or_mutated_observations_are_rejected():
    rows = snapshot(("working", "Submitted", 0.0, 5.0, 0.0, 10.0, 0.0, "", "", ""))
    with pytest.raises(ValueError, match="seal"):
        module.verify_shared_oms_observations(replace(rows, observations=()))
    with pytest.raises(ValueError, match="seal"):
        module.verify_shared_oms_observations(replace(rows,
            observations=({**rows.observations[0], "warning": "changed"},)))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True])
def test_invalid_native_observations_fail_closed(value):
    with pytest.raises(ValueError):
        snapshot(("working", "Submitted", 0.0, 5.0, value, 10.0, 0.0, "", "", ""))


def test_empty_observed_set_has_complete_sealed_root():
    rows = module.project_shared_oms_observations(run_id="shared-run",
        session_date=date(2026, 8, 18), checkpoint_sequence=42, boundary_ms=30000,
        groups={})
    assert module.verify_shared_oms_observations(rows).root["observation_count"] == 0
    assert module.verified_oms_observation_codec_rows(rows).observations == ()


def test_zero_uses_existing_canonical_price_domain():
    rows = snapshot(("working", "Submitted", -0.0, 5.0, 0.0, 10.0, 0.0, "", "", ""))
    assert rows.observations[0]["filled_quantity_bits"] == 0


@pytest.mark.parametrize("bits", [True, -1, 2 ** 64, 0x7ff0000000000000,
                                   0x8000000000000000])
def test_resealed_invalid_numeric_bits_are_not_trusted(bits):
    rows = snapshot(("working", "Submitted", 0.0, 5.0, 0.0, 10.0, 0.0, "", "", ""))
    fields = {key: value for key, value in rows.observations[0].items()
              if key != "content_hash"}
    fields["limit_price_bits"] = bits
    children = (module.codec._sealed(fields),)
    malformed = module.SharedOmsObservationSnapshot(module._root(rows.root, children), children)
    with pytest.raises(ValueError):
        module.verify_shared_oms_observations(malformed)


def reader(monkeypatch, *, failure=None, quoted=False):
    from src.trading_runtime import arte_journal_schema
    rows = snapshot(("working", "Submitted", 0.0, 5.0, 0.001, 10.0, 0.0, "", "", ""))
    preflights, queries = [], []
    monkeypatch.setattr(arte_journal_schema, "storage_preflight",
        lambda client, *, tables: preflights.append(tables))
    class Client:
        closed = False
        def execute(self, sql):
            queries.append(sql)
            root_query = f"FROM arte.{module.ROOT.name} " in sql
            values = [dict(rows.root)] if root_query else [dict(row) for row in rows.observations]
            if failure == "missing" and not root_query:
                values = []
            if failure == "duplicate" and not root_query:
                values *= 2
            if failure == "movement" and len(queries) == 3:
                values[0]["boundary_ms"] += 100
            if failure == "foreign" and root_query:
                values[0]["run_id"] = "foreign"
            if quoted:
                for value in values:
                    for name, kind in (module.ROOT if root_query else module.OBSERVATION).columns:
                        if kind.startswith("UInt"):
                            value[name] = str(value[name])
            response = "\n".join(json.dumps(value) for value in values)
            if failure == "repeated_field" and root_query:
                response = response[:-1] + ',"run_id":"shared-run"}'
            return response
        def close(self):
            self.closed = True
    return Client(), rows, queries, preflights


@pytest.mark.parametrize("quoted", [False, True])
def test_shared_reader_uses_only_bounded_central_selects(monkeypatch, quoted):
    client, rows, queries, checks = reader(monkeypatch, quoted=quoted)
    actual = module.read_shared_oms_observations(client, run_id="shared-run",
        checkpoint_sequence=42, expected_content_hash=rows.root["content_hash"])
    assert actual == rows
    assert checks == [module.TABLES]
    assert len(queries) == 3
    assert all(query.startswith("SELECT ") and " LIMIT " in query
        and "strategy_one" not in query for query in queries)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "movement", "foreign", "repeated_field"])
def test_shared_reader_rejects_incomplete_or_moving_authority(monkeypatch, failure):
    client, rows, _, _ = reader(monkeypatch, failure=failure)
    with pytest.raises(ValueError):
        module.read_shared_oms_observations(client, run_id="shared-run",
            checkpoint_sequence=42, expected_content_hash=rows.root["content_hash"])


def test_shared_reader_byte_budget_is_enforced(monkeypatch):
    client, rows, _, _ = reader(monkeypatch)
    with pytest.raises(ValueError, match="byte budget"):
        module.read_shared_oms_observations(client, run_id="shared-run",
            checkpoint_sequence=42, expected_content_hash=rows.root["content_hash"], maximum_bytes=1)


def test_owned_reader_drains_repeated_cancellation_and_closes(monkeypatch):
    client, rows, _, _ = reader(monkeypatch)
    entered, release = Event(), Event()
    execute = client.execute
    def blocked(sql):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Synthetic checkpoint reader was not released")
        return execute(sql)
    client.execute = blocked
    async def run():
        task = asyncio.create_task(module.load_shared_oms_observations(lambda: client,
            run_id="shared-run", checkpoint_sequence=42,
            expected_content_hash=rows.root["content_hash"]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not client.closed
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.closed
    asyncio.run(run())


@pytest.mark.parametrize("movement", [None, "head", "cursor", "prefix", "session", "wrong_head"])
def test_attestation_requires_unchanged_central_selection(monkeypatch, movement):
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime import arte_journal_commit_v4 as commits, arte_journal_projection as projection
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from tests.test_live_signal_completion_keeper import FakeKazoo
    client, rows, _, _ = reader(monkeypatch)
    batch = "00000000-0000-0000-0000-000000000007"
    prefix = V4CommittedPrefix("shared-run", 42, batch, "cursor", "running", (batch,))
    kazoo = FakeKazoo()
    kazoo.add_listener = lambda listener: None
    session = ManagedKeeperSession(kazoo)
    session._on_state("CONNECTED")
    heads = module.ManagedSharedOmsObservationHeadReader(session)
    path = heads.path("shared-run")
    assert path != module.codec.ManagedOmsObservationHeadReader.path("shared-run")
    value = "\n".join(("1", "shared-run", "41" if movement == "wrong_head" else "42",
                       batch, rows.root["content_hash"]))
    kazoo.create(path, value.encode())
    reads = []
    def read_prefix(*args):
        reads.append(True)
        return replace(prefix, last_sequence=43) if movement == "prefix" and len(reads) == 2 else prefix
    def cursor(*args):
        if movement == "session":
            session._on_state("SUSPENDED")
            session._on_state("CONNECTED")
        if movement == "head":
            raw, version, owner = kazoo.nodes[path]
            kazoo.nodes[path] = (raw, version + 1, owner)
        return dict(run_id="shared-run", event_sequence=42, batch_id=batch,
            boundary_ms=rows.root["boundary_ms"] + (100 if movement == "cursor" else 0),
            session_date=rows.root["session_date"])
    monkeypatch.setattr(commits, "load_verified_v4_prefix", read_prefix)
    monkeypatch.setattr(projection, "load_latest_backtest_cursor", cursor)
    async def load():
        return await module.load_attested_shared_oms_observations(lambda: client, heads, prefix=prefix)
    if movement:
        with pytest.raises((ValueError, RuntimeError), match="cursor|moved|Keeper session changed"):
            asyncio.run(load())
    else:
        assert asyncio.run(load()) == rows and len(reads) == 2
    assert client.closed

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest

from pipelines.strategy_one.strategy_forty_three_publication import (
    PublicationSource, prepare_rows, scalar_hash,
)
from src.trading_runtime.strategy_forty_three_fact_schema import (
    CONTRACTS, FACT_COLUMNS, STORAGE_POLICY, ddl,
)
from tests.test_strategy_forty_three_facts import grid


def test_arrow_insertion_preserves_sensitive_float_bits_without_decimal_transport():
    import pyarrow as pa
    import struct
    from pipelines.strategy_one.strategy_forty_three_publication import insert_facts
    facts, _, _ = prepare_rows(source(), grid([10.] * 15))
    facts[0]["previous_ten_second_mean_notional"] = 6.565
    captured = []
    client = SimpleNamespace(execute=lambda payload: captured.append(payload))
    insert_facts(client, facts)
    header, binary = captured[0].split(b"\n", 1)
    assert header.endswith(b"FORMAT ArrowStream")
    table = pa.ipc.open_stream(binary).read_all()
    actual = table["previous_ten_second_mean_notional"][0].as_py()
    assert struct.pack("<d", actual) == struct.pack("<d", 6.565)
    assert table["previous_ten_second_mean_notional"][1].as_py() is None


def source():
    return PublicationSource("a" * 64, "2026-09-03", "TEST",
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
        "b" * 64, "c" * 64, "d" * 64, "e" * 64,
        "listing:test", "symbol:test", "security:test", 123, 6000, 15_000)


def test_publication_rows_are_normalized_dense_and_bind_inputs_and_attempt():
    facts, population, coverage = prepare_rows(source(), grid([10.] * 15))
    assert len(facts) == coverage["fact_count"] == 15
    assert tuple(facts[0]) == tuple(name for name, _ in FACT_COLUMNS)
    assert len({UUID(row["fact_id"]) for row in facts}) == 15
    assert coverage["fact_hash"] == scalar_hash(facts)
    assert coverage["population_hash"] == scalar_hash((population,))
    assert coverage["source_v7_token"] == source().source_v7_token
    assert all(not isinstance(value, (list, tuple, dict)) for row in facts for value in row.values())
    changed = replace(source(), attempt_id="44444444-4444-4444-8444-444444444444")
    second, _, _ = prepare_rows(changed, grid([10.] * 15))
    assert second[0]["fact_id"] != facts[0]["fact_id"]


def test_ieee754_hash_does_not_tolerate_rounding_or_reordering():
    assert scalar_hash((dict(x=.1),)) != scalar_hash((dict(x=.10000000000000002),))
    assert scalar_hash((dict(x=0.),)) != scalar_hash((dict(x=-0.),))
    assert scalar_hash((dict(x=1.), dict(x=2.))) != scalar_hash((dict(x=2.), dict(x=1.)))


def test_wrong_population_or_incomplete_session_fails_before_publication():
    with pytest.raises(ValueError, match="tradable"):
        prepare_rows(replace(source(), ticker="LGHL"), grid([10.] * 15))
    with pytest.raises(ValueError, match="session end"):
        prepare_rows(source(), grid([10.] * 14))


def test_every_table_uses_explicit_ssd_policy_and_versioned_scalar_columns():
    statements = ddl()
    assert len(statements) == len(CONTRACTS) == 3
    assert all(f"storage_policy='{STORAGE_POLICY}'" in statement for statement in statements)
    assert all("MergeTree" in statement and "toYYYYMM(session_date)" in statement for statement in statements)
    assert all("JSON" not in kind for columns in CONTRACTS.values() for _, kind in columns)


def publication_fixture(monkeypatch, *, partial=0, sealed=False):
    import pipelines.strategy_one.strategy_forty_three_publication as producer
    facts, population, coverage = prepare_rows(source(), grid([10.] * 15))
    state = dict(facts=list(facts[:partial]), population=[population] if partial else [],
                 seals=[coverage] if sealed else [], inserts=[], deletes=[])
    class Client:
        def execute(self, query):
            assert query.startswith("SELECT count()")
            return str(len(state["facts"]))
    class Keeper:
        connected = True
        stat = SimpleNamespace(ephemeralOwner=17, czxid=21, version=0)
        def create(self, *_args, **_kwargs):
            pass
        def exists(self, _path):
            return self.stat
        def delete(self, path, *, version):
            state["deletes"].append((path, version))
    def insert(_client, table, _columns, rows):
        state["inserts"].append(table)
        key = {producer.FACT_TABLE: "facts", producer.POPULATION_TABLE: "population",
               producer.COVERAGE_TABLE: "seals"}[table]
        state[key].extend(rows)
    monkeypatch.setattr(producer, "verify_tables", lambda _: None)
    monkeypatch.setattr(producer, "read_rows", lambda *_: list(state["seals"]))
    monkeypatch.setattr(producer, "read_facts", lambda *_: list(state["facts"]))
    monkeypatch.setattr(producer, "read_population", lambda *_: list(state["population"]))
    monkeypatch.setattr(producer, "_insert_rows", insert)
    monkeypatch.setattr(producer, "insert_facts", lambda client, rows:
        insert(client, producer.FACT_TABLE, (), rows))
    return producer, Client(), Keeper(), state, coverage


def test_publication_resumes_only_exact_prefix_and_seals_after_readback(monkeypatch):
    producer, client, keeper, state, coverage = publication_fixture(monkeypatch, partial=7)
    receipt = producer.publish_unit(client, keeper, source(), grid([10.] * 15))
    assert receipt == scalar_hash((coverage,))
    assert state["inserts"] == [producer.FACT_TABLE, producer.COVERAGE_TABLE]
    assert len(state["facts"]) == 15 and len(state["seals"]) == 1
    assert len(state["deletes"]) == 1
    state["inserts"].clear()
    assert producer.publish_unit(client, keeper, source(), grid([10.] * 15)) == receipt
    assert state["inserts"] == []


def test_sealed_incomplete_product_is_not_repaired_by_retry(monkeypatch):
    producer, client, keeper, state, _ = publication_fixture(monkeypatch, partial=7, sealed=True)
    with pytest.raises(RuntimeError, match="sealed product readback"):
        producer.publish_unit(client, keeper, source(), grid([10.] * 15))
    assert state["inserts"] == []


def test_changed_partial_prefix_never_publishes_coverage(monkeypatch):
    producer, client, keeper, state, _ = publication_fixture(monkeypatch, partial=7)
    state["facts"][0] = {**state["facts"][0], "close": 11.}
    with pytest.raises(RuntimeError, match="cannot be resumed exactly"):
        producer.publish_unit(client, keeper, source(), grid([10.] * 15))
    assert state["inserts"] == [] and state["seals"] == []


def test_session_loss_does_not_delete_successor_lock_or_publish_coverage(monkeypatch):
    producer, client, keeper, state, _ = publication_fixture(monkeypatch)
    original = producer._insert_rows
    def lose_session(*args):
        original(*args)
        keeper.connected = False
        keeper.stat = SimpleNamespace(ephemeralOwner=18, czxid=22, version=0)
    monkeypatch.setattr(producer, "_insert_rows", lose_session)
    with pytest.raises(RuntimeError, match="Keeper ownership is lost"):
        producer.publish_unit(client, keeper, source(), grid([10.] * 15))
    assert state["deletes"] == [] and state["seals"] == []

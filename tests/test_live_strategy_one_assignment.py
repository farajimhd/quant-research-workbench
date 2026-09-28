"""Strategy 1 assignment facts must not reuse legacy 26-47 snapshots."""
from dataclasses import replace
import json

import pytest

from src.backend import live_strategy_one_assignment as subject
from src.backend.live_assignment_activation_join import (
    AttestedPlanMembership, PinnedAssignmentMember, PinnedWatch,
)


REVISION = "approved-1"
SESSION = "2026-08-18"
PLAN = "plan-1"
PUBLICATION = "00000000-0000-0000-0000-000000000001"


def assignment():
    return subject.project_strategy_one_assignment(
        configuration_revision_id=REVISION, session_date=SESSION,
        publication_id=PUBLICATION,
        run_plan_id=PLAN, assignment_id="assignment-1", revision=1,
        account_id="DU1", ticker="AAPL", conid=12345)


class Proof:
    def __init__(self, row):
        self.member = PinnedAssignmentMember(
            row["assignment_id"], PLAN, row["revision"], row["content_hash"])
        self.current = "a" * 64

    def read_attested_plan(self, *, configuration_revision_id, run_plan_id):
        assert (configuration_revision_id, run_plan_id) == (REVISION, PLAN)
        return AttestedPlanMembership(
            REVISION, PLAN, (self.member,), (), "a" * 64, PUBLICATION)

    def head_hash(self, *, configuration_revision_id, run_plan_id):
        assert (configuration_revision_id, run_plan_id) == (REVISION, PLAN)
        return self.current


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, sql):
        assert sql.startswith("SELECT ")
        assert "FROM arte.strategy_one_live_assignment_v1" in sql
        self.queries.append(sql)
        return "\n".join(json.dumps(row) for row in self.rows)


@pytest.fixture(autouse=True)
def storage(monkeypatch):
    calls = []
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda client, *, tables: calls.append(tables))
    yield calls


def test_strategy_one_assignment_is_scalar_ssd_and_immutable():
    ddl = subject.TABLE.ddl()
    assert "storage_policy = 'live_market_ssd'" in ddl
    assert "PARTITION BY toYYYYMM(session_date)" in ddl
    assert "ORDER BY (configuration_revision_id, session_date, publication_id, run_plan_id, assignment_id, revision)" in ddl
    assert not any(kind in ddl for kind in ("JSON", "Array(", "Map(", "Blob"))
    first = assignment()
    assert first == assignment()
    assert first["strategy_number"] == 1


def test_cold_join_reads_only_exact_attested_assignment(storage):
    row = assignment()
    client = Client([row])
    result = subject.cold_read_strategy_one_assignments(
        client, Proof(row), configuration_revision_id=REVISION,
        session_date=SESSION, run_plan_id=PLAN)
    assert result == (subject.StrategyOneLiveAssignment(
        "assignment-1", 1, "DU1", "AAPL", 12345, row["content_hash"]),)
    assert storage == [(subject.TABLE,)]
    assert len(client.queries) == 1
    assert "LIMIT 100001" in client.queries[0]
    assert f"publication_id=toUUID('{PUBLICATION}')" in client.queries[0]


@pytest.mark.parametrize("rows,reason", [
    ([], "differ from attested"),
    ([assignment(), assignment()], "differ from attested"),
    ([{**assignment(), "account_id": "DU2"}], "pinned scalar fact"),
    ([{**assignment(), "revision": 2}], "pinned scalar fact"),
    ([{**assignment(), "ticker": "aapl"}], "scope or instrument"),
])
def test_cold_join_rejects_missing_duplicate_or_changed_facts(rows, reason):
    with pytest.raises(ValueError, match=reason):
        subject.cold_read_strategy_one_assignments(
            Client(rows), Proof(assignment()),
            configuration_revision_id=REVISION,
            session_date=SESSION, run_plan_id=PLAN)


def test_cold_join_rejects_changed_head_and_membership_revision():
    row = assignment()
    proof = Proof(row)
    proof.current = "b" * 64
    with pytest.raises(RuntimeError, match="head changed"):
        subject.cold_read_strategy_one_assignments(
            Client([row]), proof, configuration_revision_id=REVISION,
            session_date=SESSION, run_plan_id=PLAN)
    proof = Proof(row)
    proof.member = replace(proof.member, base_sequence=2)
    with pytest.raises(ValueError, match="pinned scalar fact"):
        subject.cold_read_strategy_one_assignments(
            Client([row]), proof, configuration_revision_id=REVISION,
            session_date=SESSION, run_plan_id=PLAN)


def test_cold_join_rejects_unbound_publication():
    row = assignment()
    proof = Proof(row)
    original = proof.read_attested_plan
    proof.read_attested_plan = lambda **kwargs: replace(
        original(**kwargs), publication_id="")
    with pytest.raises(ValueError, match="publication ID"):
        subject.cold_read_strategy_one_assignments(
            Client([row]), proof, configuration_revision_id=REVISION,
            session_date=SESSION, run_plan_id=PLAN)


def test_cold_join_requires_activated_watch_assignment():
    row = assignment()
    proof = Proof(row)
    original = proof.read_attested_plan
    proof.read_attested_plan = lambda **kwargs: replace(
        original(**kwargs),
        activated_watches=(PinnedWatch("MSFT", "profile-1", "book-1"),))
    with pytest.raises(ValueError, match="watch lacks"):
        subject.cold_read_strategy_one_assignments(
            Client([row]), proof, configuration_revision_id=REVISION,
            session_date=SESSION, run_plan_id=PLAN)


def test_cold_join_rejects_duplicate_account_ticker():
    first = assignment()
    second = subject.project_strategy_one_assignment(
        configuration_revision_id=REVISION, session_date=SESSION,
        publication_id=PUBLICATION,
        run_plan_id=PLAN, assignment_id="assignment-2", revision=1,
        account_id="DU1", ticker="AAPL", conid=12345)
    proof = Proof(first)
    original = proof.read_attested_plan
    proof.read_attested_plan = lambda **kwargs: replace(
        original(**kwargs), assignments=(proof.member, PinnedAssignmentMember(
            second["assignment_id"], PLAN, 1, second["content_hash"])))
    with pytest.raises(ValueError, match="repeats account and ticker"):
        subject.cold_read_strategy_one_assignments(
            Client([first, second]), proof, configuration_revision_id=REVISION,
            session_date=SESSION, run_plan_id=PLAN)


def test_producer_publishes_exact_scalar_rows_before_membership(monkeypatch):
    monkeypatch.setattr(subject, "storage_preflight", lambda *_args, **_kwargs: None)
    class Writer:
        def __init__(self):
            self.rows = []
            self.calls = []
        def execute(self, sql):
            self.calls.append(sql)
            if sql.startswith("SELECT "):
                return "\n".join(json.dumps(row) for row in self.rows)
            assert sql.startswith("INSERT INTO arte.strategy_one_live_assignment_v1")
            self.rows = [json.loads(line) for line in sql.split("FORMAT JSONEachRow\n", 1)[1].splitlines()]
            return ""
    writer = Writer()
    producer = subject.StrategyOneAssignmentProducer(writer, (
        subject.StrategyOneAssignmentSpec(PLAN, "assignment-1", 1, "DU1", "AAPL", 12345),))
    pinned = producer.publish(configuration_revision_id=REVISION,
                              session_key=SESSION, publication_id=PUBLICATION)
    assert pinned[0].base_hash == writer.rows[0]["content_hash"]
    assert [call.startswith("SELECT ") for call in writer.calls] == [True, False, True]
    with pytest.raises(RuntimeError, match="already used"):
        producer.publish(configuration_revision_id=REVISION,
                         session_key=SESSION, publication_id=PUBLICATION)

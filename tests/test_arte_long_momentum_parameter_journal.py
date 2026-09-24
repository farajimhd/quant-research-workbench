from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_journal_schema import (
    TABLES as ACTIVE_TABLES, long_momentum_parameter_upgrade_ddl, schema_ddl,
)
from src.trading_runtime.arte_long_momentum_parameter_journal import (
    COMMIT_TABLE, PARAMETER_TABLES, UncommittedParameterSnapshot,
    load_attested_parameters, load_diagnostic_parameters, publish_parameters,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, resolve_long_momentum_parameters


KEY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
           strategy_revision=47, snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f",
           session="2026-09-24")


class FakeStorage:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.inserts: list[str] = []
        self.fail_after_insert: str | None = None

    def insert(self, table: str, rows: list[dict]) -> None:
        self.inserts.append(table)
        self.rows.setdefault(table, []).extend(deepcopy(rows))
        if self.fail_after_insert == table:
            self.fail_after_insert = None
            raise RuntimeError("ambiguous storage acknowledgment")

    def read(self, table: str, identity: dict) -> list[dict]:
        return [deepcopy(row) for row in self.rows.get(table, [])
                if all(row[key] == value for key, value in identity.items())]


class FakeDurableAdmission:
    def __init__(self, claims: dict | None = None) -> None:
        self.claims = {} if claims is None else claims

    def begin_once(self, identity: dict) -> bool:
        key = tuple(sorted(identity.items()))
        if key in self.claims:
            return False
        self.claims[key] = "started"
        return True

    def mark_committed(self, identity: dict, content_hash: str) -> None:
        self.claims[tuple(sorted(identity.items()))] = content_hash

    def assert_current(self, identity: dict) -> None:
        if self.claims.get(tuple(sorted(identity.items()))) != "started":
            raise RuntimeError("parameter claim is not current")

    def read_claim(self, identity: dict) -> SimpleNamespace | None:
        value = self.claims.get(tuple(sorted(identity.items())))
        if value is None:
            return None
        return SimpleNamespace(state="started" if value == "started" else "committed",
                               content_hash=None if value == "started" else value)


def test_operator_upgrade_is_staged_and_explicit_policy() -> None:
    assert long_momentum_parameter_upgrade_ddl() == tuple(table.ddl() for table in PARAMETER_TABLES)
    assert len({table.name for table in PARAMETER_TABLES}) == len(PARAMETER_TABLES)
    assert all(table not in ACTIVE_TABLES for table in PARAMETER_TABLES)
    assert all(table.ddl() not in schema_ddl() for table in PARAMETER_TABLES)
    assert all("storage_policy = 'live_market_ssd'" in ddl for ddl in long_momentum_parameter_upgrade_ddl())
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in PARAMETER_TABLES for _, kind in table.columns)


def test_fake_publication_commits_last_and_cold_recovers_all_families() -> None:
    storage = FakeStorage()
    admission = FakeDurableAdmission()
    parameters = resolve_long_momentum_parameters(revision=47)
    assert load_diagnostic_parameters(storage, **KEY) is None
    assert load_attested_parameters(storage, admission, **KEY) is None
    receipt = publish_parameters(storage, parameters, admission=admission, **KEY)
    assert receipt["child_count"] > 14
    assert storage.inserts[-1] == COMMIT_TABLE.name
    assert load_diagnostic_parameters(storage, **KEY) == parameters
    assert load_attested_parameters(storage, admission, **KEY) == parameters
    before = list(storage.inserts)
    assert publish_parameters(storage, parameters, admission=admission, **KEY) == receipt
    assert storage.inserts == before
    table = PARAMETER_TABLES[0].name
    storage.rows[table][0]["content_hash"] = "0" * 64
    with pytest.raises(ValueError, match="fence"):
        load_diagnostic_parameters(storage, **KEY)


def test_fake_ambiguous_commit_reconciles_but_missing_child_fails_closed() -> None:
    parameters = resolve_long_momentum_parameters(revision=47)
    storage = FakeStorage()
    admission = FakeDurableAdmission()
    storage.fail_after_insert = COMMIT_TABLE.name
    assert publish_parameters(storage, parameters, admission=admission, **KEY)["child_count"] > 14
    assert load_diagnostic_parameters(storage, **KEY) == parameters
    storage = FakeStorage()
    admission = FakeDurableAdmission()
    storage.fail_after_insert = PARAMETER_TABLES[0].name
    with pytest.raises(RuntimeError, match="ambiguous"):
        publish_parameters(storage, parameters, admission=admission, **KEY)
    assert load_diagnostic_parameters(storage, **KEY) is None
    assert COMMIT_TABLE.name not in storage.inserts
    # Claim survives a publisher restart and forbids a blind second INSERT.
    restarted = FakeDurableAdmission(admission.claims)
    before = list(storage.inserts)
    with pytest.raises(UncommittedParameterSnapshot, match="already attempted"):
        publish_parameters(storage, parameters, admission=restarted, **KEY)
    assert storage.inserts == before


def test_uncommitted_partial_children_fail_before_any_insert() -> None:
    storage = FakeStorage()
    storage.rows[PARAMETER_TABLES[0].name] = [{**KEY, "content_hash": "0" * 64}]
    with pytest.raises(UncommittedParameterSnapshot, match="uncommitted child rows"):
        publish_parameters(storage, resolve_long_momentum_parameters(revision=47),
                           admission=FakeDurableAdmission(), **KEY)
    assert storage.inserts == []

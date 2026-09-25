from __future__ import annotations

from datetime import date
import json
from unittest.mock import patch

import pytest

from src.backend.live_activation_cold_bootstrap import (
    _audit_source_orphans, ActivationRecoveryUnfenced,
    audit_activation_checkpoint_under_cooperative_fences,
    audit_receipt_defined_activation_prefix_under_fences,
    cold_audit_activation_watches, read_attested_activation_prefix,
    cold_recover_activation_checkpoint,
)
from src.backend.live_signal_completion_keeper import completion_resource
from src.backend.live_signal_work_completion import project_completion
from src.backend.signal_dispatch_typed_cursor import (
    project_dispatch_ack, project_dispatch_intents,
)
from src.trading_runtime.arte_activation_projection import (
    prepare_activation_rows, project_activation, publish_activation,
)
from tests.test_arte_activation_projection import _MemoryClient
from tests.test_live_signal_work_completion import Keeper, Storage
from tests.test_signal_dispatch_typed_cursor import ColdStorage


SESSION = date(2026, 9, 24)
EVENT_ID = "a" * 64


class Authority:
    def __init__(self, occurrence):
        self.occurrence = occurrence

    def read_exact(self, event_id):
        return self.occurrence if event_id == EVENT_ID else None


class ActivationKeeper:
    def portfolio_admission_lease_is_current(self, resource_id, *, owner_id, epoch):
        return True


def _case():
    at = "2026-09-24T14:00:00+00:00"
    occurrence = {"event_id": EVENT_ID, "signal_id": EVENT_ID,
                  "ticker": "ABC", "signal_stream_id": "stream-1",
                  "event_time": at, "effective_at": at,
                  "evidence": {"market.last_price": 3.83},
                  "field_evidence": {}}
    delivery = {"delivery_id": f"plan-1:{EVENT_ID}", "run_plan_id": "plan-1",
                "profile_id": "profile-1", "book_id": "default", "ticker": "ABC",
                "signal_stream_id": "stream-1", "event_id": EVENT_ID,
                "event_time": at, "occurrence": occurrence}
    activation_client = _MemoryClient()
    activation_hash = publish_activation(
        activation_client, project_activation(delivery), keeper=ActivationKeeper(),
        owner_id="worker-1", epoch=1)
    assert activation_hash == prepare_activation_rows(project_activation(delivery))[
        "trading_activation_v1"][0]["content_hash"]
    intents = project_dispatch_intents(
        [delivery], session_key=SESSION.isoformat(), source_batch_sequence=1,
        source_cursor_commit_hash="b" * 64,
        configuration_revision_id="approved-1",
        occurrence_authority=Authority(occurrence))
    acks = project_dispatch_ack(
        intents, [{"delivery_id": delivery["delivery_id"],
                   "ack_kind": "activation_durable",
                   "activation_receipt_hash": activation_hash}],
        acknowledged_at="2026-09-24T14:00:01+00:00")
    completion_storage, completion_keeper = Storage(), Keeper()
    completion = project_completion(
        intents, acks, ordinal=0, processed_at="2026-09-24T14:00:02+00:00",
        keeper_owner_id="owner-1", keeper_epoch=1)
    completion_storage.insert_completion_row(completion.row)
    resource = completion_resource(SESSION.isoformat(), 1, 0, delivery["delivery_id"])
    completion_keeper.proof = (resource, "owner-1", 1, completion.row["content_hash"])
    return (activation_client, ColdStorage(intents, acks),
            completion_storage, completion_keeper, delivery)


def _audit(case):
    activation, dispatch, completion, keeper, _ = case
    return cold_audit_activation_watches(
        activation, dispatch, completion, keeper, session_date=SESSION,
        source_commit_hashes=("b" * 64,), configuration_revision_id="approved-1")


def test_cold_join_recovers_only_exact_completed_activation_without_sqlite() -> None:
    case = _case()
    with patch("src.backend.trading_runtime_service.trading_journal",
               side_effect=AssertionError("cold audit must not read SQLite")):
        restored = _audit(case)
    assert len(restored) == 1
    assert restored[0]["delivery_id"] == case[-1]["delivery_id"]


def test_missing_completion_or_unbacked_activation_fails_closed() -> None:
    case = _case()
    case[2].rows.clear()
    with pytest.raises(ValueError, match="absent or uncertain"):
        _audit(case)
    case = _case()
    case[0].rows["trading_activation_v1"].clear()
    with pytest.raises((ValueError, RuntimeError), match="Activation|activation"):
        _audit(case)


def test_activation_receipt_hash_must_match_dispatch_ack() -> None:
    case = _case()
    case[1].rows["signal_dispatch_ack_typed_v1"][0]["activation_receipt_hash"] = "d" * 64
    with pytest.raises(ValueError, match="fence"):
        _audit(case)


def test_cold_inventory_bounds_extra_event_even_under_same_watch() -> None:
    case = _case()
    duplicate = dict(case[0].rows["trading_activation_v1"][0], event_id="f" * 64)
    case[0].rows["trading_activation_v1"].append(duplicate)
    with pytest.raises(RuntimeError, match="expected coverage"):
        _audit(case)


def test_typed_checkpoint_requires_stable_keeper_source_head(monkeypatch) -> None:
    case = _case()
    from types import SimpleNamespace
    source_head = SimpleNamespace(
        session_key=SESSION.isoformat(), batch_sequence=1,
        cursor_commit_hash="b" * 64,
        configuration_revision="approved-1", source_revision="source-1")
    class KeeperHead:
        changed = False
        def acquire(self, session_key, *, owner_id):
            return 1
        def is_current(self, session_key, *, owner_id, epoch):
            return True
        def release(self, session_key, *, owner_id, epoch):
            return True
        def read_head(self, session_key):
            assert session_key == SESSION.isoformat()
            return (SimpleNamespace(**{**vars(source_head), "cursor_commit_hash": "c" * 64})
                    if self.changed else source_head)
    class Source:
        pass
    class Client:
        def execute(self, sql):
            if sql.startswith("SELECT DISTINCT "):
                return ""
            assert sql.startswith("SELECT schema_version,session_key,batch_sequence,")
            assert "ORDER BY batch_sequence LIMIT 100001 FORMAT JSONEachRow" in sql
            return json.dumps({"batch_sequence": 1, "content_hash": "b" * 64})
    class Fence:
        def acquire(self, session_key, *, owner_id):
            return 1
        def is_current(self, session_key, *, owner_id, epoch):
            return True
        def release(self, session_key, *, owner_id, epoch):
            return True
    monkeypatch.setattr("src.backend.live_activation_cold_bootstrap.recover_committed_head",
                        lambda *args, **kwargs: SimpleNamespace(
                            sequence=1, content_hash="b" * 64))
    monkeypatch.setattr("src.backend.live_activation_cold_bootstrap.canonical_row",
                        lambda table, row: row)
    keeper = KeeperHead()
    activation, dispatch, completion, completion_keeper, delivery = case
    kwargs = dict(session_date=SESSION, configuration_revision_id="approved-1",
                  source_revision_id="source-1", catalogs={}, owner_id="recovery",
                  activation_fence=Fence())
    with patch("src.backend.trading_runtime_service.trading_journal",
               side_effect=AssertionError("typed checkpoint must not read SQLite")):
        restored = audit_activation_checkpoint_under_cooperative_fences(
            activation, Source(), Client(), keeper, dispatch, completion,
            completion_keeper, **kwargs)
    assert restored[0]["delivery_id"] == delivery["delivery_id"]
    activation.rows["trading_activation_v1"].append(
        dict(activation.rows["trading_activation_v1"][0], event_id="f" * 64))
    admitted = audit_receipt_defined_activation_prefix_under_fences(
        activation, Source(), Client(), keeper, dispatch, completion,
        completion_keeper, **kwargs)
    assert [row["delivery_id"] for row in admitted] == [delivery["delivery_id"]]
    activation.rows["trading_activation_v1"].pop()
    keeper.changed = True
    with pytest.raises(ValueError, match="cursor differs"):
        audit_activation_checkpoint_under_cooperative_fences(
            activation, Source(), Client(), keeper, dispatch, completion,
            completion_keeper, **kwargs)
    keeper.changed = False
    original_audit = cold_audit_activation_watches
    def race(*args, **audit_kwargs):
        result = original_audit(*args, **audit_kwargs)
        keeper.changed = True
        return result
    monkeypatch.setattr(
        "src.backend.live_activation_cold_bootstrap.cold_audit_activation_watches",
        race)
    with pytest.raises(RuntimeError, match="head changed"):
        audit_activation_checkpoint_under_cooperative_fences(
            activation, Source(), Client(), keeper, dispatch, completion,
            completion_keeper, **kwargs)
    class OverflowClient(Client):
        def execute(self, sql):
            assert "LIMIT 2 FORMAT JSONEachRow" in sql
            row = {"batch_sequence": 1, "content_hash": "b" * 64}
            return "\n".join((json.dumps(row), json.dumps(row)))
    keeper.changed = False
    with pytest.raises(ValueError, match="exceeds recovery bound"):
        audit_activation_checkpoint_under_cooperative_fences(
            activation, Source(), OverflowClient(), keeper, dispatch,
            completion, completion_keeper, max_source_batches=1, **kwargs)


def test_typed_checkpoint_rejects_missing_or_advanced_source(monkeypatch) -> None:
    case = _case()
    from types import SimpleNamespace
    class KeeperHead:
        def acquire(self, session_key, *, owner_id):
            return 1
        def is_current(self, session_key, *, owner_id, epoch):
            return True
        def release(self, session_key, *, owner_id, epoch):
            return True
        def read_head(self, session_key):
            return SimpleNamespace(
                session_key=session_key, batch_sequence=2,
                cursor_commit_hash="b" * 64,
                configuration_revision="approved-1", source_revision="source-1")
    monkeypatch.setattr("src.backend.live_activation_cold_bootstrap.recover_committed_head",
                        lambda *args, **kwargs: SimpleNamespace(
                            sequence=1, content_hash="b" * 64))
    class Client:
        def execute(self, sql):
            raise AssertionError("cursor mismatch must precede CH inventory")
    class Fence:
        def acquire(self, session_key, *, owner_id):
            return 1
        def is_current(self, session_key, *, owner_id, epoch):
            return True
        def release(self, session_key, *, owner_id, epoch):
            return True
    with pytest.raises(ValueError, match="cursor differs"):
        audit_activation_checkpoint_under_cooperative_fences(
            case[0], object(), Client(), KeeperHead(), case[1], case[2], case[3],
            session_date=SESSION, configuration_revision_id="approved-1",
            source_revision_id="source-1", catalogs={}, owner_id="recovery",
            activation_fence=Fence())


def test_source_orphan_inventory_rejects_uncommitted_batch_and_event() -> None:
    class Client:
        extra_batch = False
        extra_event = False
        def execute(self, sql):
            assert "WHERE session_key='2026-09-24'" in sql
            assert "LIMIT " in sql and sql.endswith(" FORMAT JSONEachRow")
            if "SELECT DISTINCT batch_sequence " in sql:
                values = [1, 2] if self.extra_batch and "state_delta" in sql else [1]
                return "\n".join(json.dumps({"batch_sequence": value}) for value in values)
            if "SELECT DISTINCT event_id " in sql:
                values = ["a" * 64]
                if self.extra_event and "python_rule" in sql:
                    values.append("f" * 64)
                return "\n".join(json.dumps({"event_id": value}) for value in values)
            raise AssertionError(sql)
    client = Client()
    _audit_source_orphans(client, session_key=SESSION.isoformat(),
                          head_sequence=1, max_occurrences=2)
    client.extra_batch = True
    with pytest.raises(ValueError, match="uncommitted"):
        _audit_source_orphans(client, session_key=SESSION.isoformat(),
                              head_sequence=1, max_occurrences=2)
    client.extra_batch, client.extra_event = False, True
    with pytest.raises(ValueError, match="orphan child"):
        _audit_source_orphans(client, session_key=SESSION.isoformat(),
                              head_sequence=1, max_occurrences=2)


def test_activation_session_fence_contention_prevents_any_cold_ch_read() -> None:
    class SourceKeeper:
        released = False
        def acquire(self, session_key, *, owner_id):
            return 1
        def is_current(self, session_key, *, owner_id, epoch):
            return True
        def release(self, session_key, *, owner_id, epoch):
            self.released = True
            return True
    class HeldActivation:
        def acquire(self, session_key, *, owner_id):
            return None
    class Forbidden:
        def execute(self, sql):
            raise AssertionError("contended fence must precede CH read")
    keeper = SourceKeeper()
    with pytest.raises(RuntimeError, match="in progress"):
        audit_activation_checkpoint_under_cooperative_fences(
            Forbidden(), object(), Forbidden(), keeper, object(),
            object(), object(), session_date=SESSION,
            configuration_revision_id="approved-1", source_revision_id="source-1",
            catalogs={}, owner_id="recovery", activation_fence=HeldActivation())
    assert keeper.released


def test_live_activation_recovery_gate_blocks_before_any_io() -> None:
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("unsafe recovery gate must not access external authority")
    with pytest.raises(ActivationRecoveryUnfenced, match="server-enforced INSERT epoch"):
        cold_recover_activation_checkpoint(
            Forbidden(), Forbidden(), Forbidden(), Forbidden(),
            Forbidden(), Forbidden(), Forbidden(),
            session_date=SESSION, configuration_revision_id="approved-1",
            source_revision_id="source-1", catalogs={}, owner_id="recovery",
            activation_fence=Forbidden())


def test_delayed_activation_row_can_invalidate_prior_diagnostic_inventory() -> None:
    case = _case()
    assert len(_audit(case)) == 1
    # Models an INSERT sent before an old Keeper session was lost, then
    # delivered by ClickHouse only after the diagnostic scan finished.
    case[0].rows["trading_activation_v1"].append(
        dict(case[0].rows["trading_activation_v1"][0], event_id="f" * 64))
    with pytest.raises(RuntimeError, match="expected coverage"):
        _audit(case)


def test_attested_prefix_admits_only_ack_receipts_not_late_orphan_rows() -> None:
    case = _case()
    activation, dispatch, completion, keeper, delivery = case
    original = activation.execute
    def no_day_scan(sql):
        if sql.startswith("SELECT DISTINCT run_plan_id,ticker,event_id"):
            raise AssertionError("receipt reader must not inventory unadmitted rows")
        return original(sql)
    activation.execute = no_day_scan
    activation.rows["trading_activation_v1"].append(
        dict(activation.rows["trading_activation_v1"][0], event_id="f" * 64))
    result = read_attested_activation_prefix(
        activation, dispatch, completion, keeper, session_date=SESSION,
        source_commit_hashes=("b" * 64,),
        configuration_revision_id="approved-1")
    assert [item["delivery_id"] for item in result] == [delivery["delivery_id"]]


def test_attested_prefix_rejects_missing_or_conflicting_receipt_rows() -> None:
    case = _case()
    activation, dispatch, completion, keeper, _ = case
    kwargs = dict(session_date=SESSION, source_commit_hashes=("b" * 64,),
                  configuration_revision_id="approved-1")
    activation.rows["trading_activation_v1"].clear()
    with pytest.raises((ValueError, RuntimeError), match="Activation|activation"):
        read_attested_activation_prefix(activation, dispatch, completion,
                                        keeper, **kwargs)
    case = _case()
    activation, dispatch, completion, keeper, _ = case
    keeper.proof = None
    with pytest.raises(ValueError, match="Keeper attestation"):
        read_attested_activation_prefix(activation, dispatch, completion,
                                        keeper, **kwargs)
    case = _case()
    activation, dispatch, completion, keeper, _ = case
    activation.rows["trading_activation_v1"].append(
        dict(activation.rows["trading_activation_v1"][0]))
    with pytest.raises((ValueError, RuntimeError), match="Activation|activation"):
        read_attested_activation_prefix(activation, dispatch, completion,
                                        keeper, **kwargs)

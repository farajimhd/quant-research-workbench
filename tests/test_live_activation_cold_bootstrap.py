from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from src.backend.live_activation_cold_bootstrap import cold_audit_activation_watches
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

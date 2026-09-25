"""Inactive exact cold join of dispatch, completion and activation watches.

The source hash prefix must come from a separately sealed Signal Stream head.
This audit performs only control-plane reads and does not install watches or
authorize trading: assignment/configuration and broker recovery remain absent.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from src.backend.live_signal_work_completion import (
    CompletionKeeper, CompletionStorage, read_completed_dispatch_prefix,
)
from src.backend.signal_dispatch_typed_cursor import DispatchColdStorage
from src.trading_runtime.arte_activation_projection import (
    load_day_activations, prepare_activation_rows, project_activation,
)


def cold_audit_activation_watches(
    activation_client: Any, dispatch_storage: DispatchColdStorage,
    completion_storage: CompletionStorage, completion_keeper: CompletionKeeper, *,
    session_date: date, source_commit_hashes: tuple[str, ...],
    configuration_revision_id: str,
) -> tuple[dict[str, Any], ...]:
    """Require exact one-to-one durable ACK, completion and activation proof."""
    if type(session_date) is not date:
        raise ValueError("activation cold audit requires a session date")
    proofs = read_completed_dispatch_prefix(
        dispatch_storage, completion_storage, completion_keeper,
        session_key=session_date.isoformat(),
        source_commit_hashes=source_commit_hashes,
        configuration_revision_id=configuration_revision_id)
    expected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    watches: set[tuple[str, str]] = set()
    for proof in proofs:
        intents, acks = proof.materialize()
        intent, ack = intents["intents"][proof.ordinal], acks["acks"][proof.ordinal]
        delivery_id = intent["delivery_id"]
        watch = (intent["run_plan_id"], intent["ticker"])
        if delivery_id in expected or watch in watches:
            raise ValueError("cold dispatch has duplicate delivery or activation watch")
        expected[delivery_id] = (intent, ack)
        watches.add(watch)
    activations = load_day_activations(activation_client, session_date=session_date)
    if len(activations) != len(expected):
        raise ValueError("cold activation inventory differs from completed dispatch")
    observed: set[str] = set()
    result = []
    for activation in activations:
        delivery_id = activation["delivery_id"]
        if delivery_id in observed or delivery_id not in expected:
            raise ValueError("cold activation is duplicate or outside completed dispatch")
        observed.add(delivery_id)
        intent, ack = expected[delivery_id]
        if any(activation.get(key) != intent[key] for key in (
                "run_plan_id", "profile_id", "book_id", "ticker",
                "signal_stream_id", "event_id")):
            raise ValueError("cold activation identity differs from dispatch")
        parent = prepare_activation_rows(project_activation(activation))[
            "trading_activation_v1"][0]
        if (parent["event_time"] != intent["event_time"]
                or parent["content_hash"] != ack["activation_receipt_hash"]):
            raise ValueError("cold activation content differs from durable ACK")
        result.append(activation)
    if observed != set(expected):
        raise ValueError("cold activation delivery coverage is incomplete")
    return tuple(result)

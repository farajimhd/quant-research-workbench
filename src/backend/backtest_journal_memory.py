"""Storage-neutral in-process Backtest journal state.

This is the command-side buffer for the ClickHouse Backtest journal.  It never
opens SQLite or writes files.  Publication and recovery are owned by the
fenced ClickHouse adapter; this buffer is not itself a durability authority.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
from itertools import chain
from threading import RLock
from typing import Any, Iterable, Mapping
from uuid import uuid4

from src.request_context import causal_identity, current_request_identity
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


class BacktestMemoryJournal:
    """Fast deterministic operational state, pending asynchronous publication."""

    def __init__(self, *, run_id: str, max_pending_records: int = 65_536,
                 initial_sequence: int = 0) -> None:
        if (not run_id or max_pending_records < 1
                or type(initial_sequence) is not int or initial_sequence < 0):
            raise ValueError("Backtest journal requires a run and positive buffer bound")
        self.run_id = run_id
        self.max_pending_records = max_pending_records
        self._records: list[JournalRecord] = []
        # UI-only counters: O(1) updates, no record scan or ClickHouse read on
        # the execution path. These are provisional until the V4 writer fences.
        self._live_counts = {"signals": 0, "intents": 0, "commands": 0, "fills": 0}
        self._strategy_one_entries: dict[str, tuple[Any, date]] = {}
        self._strategy_one_adds: dict[str, tuple[Any, date]] = {}
        self._strategy_one_protection: dict[str, Any] = {}
        self._numbered_session_exits: dict[str, Any] = {}
        self._followthrough_exits: dict[str, Any] = {}
        self._followthrough_intents: dict[str, Any] = {}
        self._entry_assignments: dict[str, str] = {}
        self._oms_groups: dict[str, Any] = {}
        self._oms_admissions: dict[str, dict[str, Any] | None] = {}
        self._order_requests: dict[str, Any] = {}
        self._backtest_progress: dict[str, dict[str, Any]] = {}
        # Admission evidence is the immutable creation fact, never the mutable
        # reservation state after a fill, release, or cancellation.
        self._reservation_creations: dict[tuple[str, str], dict[str, Any]] = {}
        self._base_sequence = initial_sequence
        self._next_sequence = initial_sequence
        self._fenced_sequence = initial_sequence
        self._by_identity: dict[tuple[str, str, str], JournalRecord] = {}
        self._signal_records: list[JournalRecord] = []
        self._protection_records: list[JournalRecord] = []
        # Effective protection evidence is queried for every OMS transition.
        # Index by its exact causal owner so unrelated historical amendments
        # are never rescanned as the Backtest journal grows.
        self._effective_protection_records: dict[
            tuple[object, object, str], list[JournalRecord]
        ] = {}
        self._checkpoint: dict[str, Any] | None = None
        self._portfolio_states: dict[str, dict[str, Any]] = {}
        self._order_states: dict[str, dict[str, Any]] = {}
        self._assignments: dict[str, dict[str, Any]] = {}
        self._leases: dict[str, dict[str, Any]] = {}
        self._campaign_ownership: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Backtest journal is closed")

    def append(self, *, run_id: str, category: str, entity_type: str,
               entity_id: str, payload: dict[str, Any], account_id: str = "",
               event_time: datetime | None = None) -> JournalRecord:
        return self.append_many([dict(run_id=run_id, category=category,
            entity_type=entity_type, entity_id=entity_id, payload=payload,
            account_id=account_id, event_time=event_time)])[0]

    def append_strategy_one_intent(
        self, *, intent: Any, proposal: Any, session_date: date,
        account_id: str, strategy_id: str, strategy_revision: int,
    ) -> JournalRecord:
        """Atomically retain one typed intent and its bounded causal sidecar.

        The sidecar is in-memory only until the V4 writer seals its named
        columns. It is never serialized as metadata or written to disk.
        """
        from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent

        if (intent != strategy_one_entry_intent(proposal, session_date=session_date)
                or account_id != proposal.account_id or not strategy_id
                or type(strategy_revision) is not int or strategy_revision != proposal.strategy_number):
            raise ValueError("Strategy 1 journal intent differs from its numbered proposal")
        with self._lock:
            record = self.append(
                run_id=self.run_id, category="strategy", entity_type="strategy_intent",
                entity_id=intent.intent_id, account_id=account_id,
                event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": strategy_id,
                         "strategy_revision": strategy_revision})
            self._strategy_one_entries[record.record_id] = (proposal, session_date)
            self._entry_assignments[intent.intent_id] = proposal.assignment_id
            return record

    def strategy_one_entry_for_record(self, record_id: str) -> tuple[Any, date] | None:
        """Return only a still-unfenced proposal for the projection worker."""
        with self._lock:
            return self._strategy_one_entries.get(record_id)

    def append_strategy_one_add_intent(
        self, *, intent: Any, proposal: Any, session_date: date,
        account_id: str, strategy_id: str, strategy_revision: int,
    ) -> JournalRecord:
        """Retain exact add evidence until its normalized V4 child is fenced."""
        from src.trading_runtime.strategy_one_intent import strategy_one_add_intent

        if (intent != strategy_one_add_intent(proposal, session_date=session_date)
                or account_id != proposal.account_id or not strategy_id
                or strategy_revision != proposal.strategy_number):
            raise ValueError("Strategy 1 add journal differs from numbered proposal")
        with self._lock:
            record = self.append(
                run_id=self.run_id, category="strategy",
                entity_type="strategy_intent", entity_id=intent.intent_id,
                account_id=account_id, event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": strategy_id,
                         "strategy_revision": strategy_revision})
            self._strategy_one_adds[record.record_id] = (proposal, session_date)
            return record

    def strategy_one_add_for_record(self, record_id: str) -> tuple[Any, date] | None:
        with self._lock:
            return self._strategy_one_adds.get(record_id)

    def attach_backtest_progress(self, record: JournalRecord,
                                 state: dict[str, Any]) -> None:
        """Freeze scalar progress beside its unfenced cursor, never as a blob."""
        with self._lock:
            self._require_open()
            if (record.run_id != self.run_id
                    or (record.category, record.entity_type) !=
                    ("checkpoint", "market_boundary")
                    or record.sequence <= self._fenced_sequence
                    or record.sequence > self._next_sequence
                    or self._records[record.sequence - self._base_sequence - 1] != record):
                raise ValueError("Backtest progress requires an unfenced market cursor")
            frozen = deepcopy(state)
            prior = self._backtest_progress.get(record.record_id)
            if prior is not None and prior != frozen:
                raise ValueError("Backtest progress changed during cursor retry")
            self._backtest_progress[record.record_id] = frozen

    def backtest_progress_for_record(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._backtest_progress.get(record_id)
            return deepcopy(value) if value is not None else None

    def append_strategy_one_protection_intent(
        self, *, intent: Any, account_id: str, strategy_id: str,
        strategy_revision: int,
    ) -> JournalRecord:
        """Retain the exact scalar amendment source until its V4 fence."""
        from src.trading_runtime.signals import StrategyIntent
        from src.trading_runtime.strategy_one_contract import STRATEGY_ID

        if (not isinstance(intent, StrategyIntent)
                or intent.action not in {
                    "replace_protective_stop", "replace_profit_target"}
                or intent.metadata or not account_id
                or strategy_id != STRATEGY_ID
                or type(strategy_revision) is not int or strategy_revision not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
                or (intent.action == "replace_profit_target"
                    and (intent.reason != "ordinal_resistance_target"
                         or intent.profit_target_price is None
                         or intent.invalidation_price is not None))
                or (intent.action == "replace_protective_stop"
                    and (intent.reason not in {
                        "completed_30s_bar_low", "three_resistance_step_stop"}
                         or intent.invalidation_price is None
                         or intent.profit_target_price is not None))):
            raise ValueError("Strategy 1 protection source is not normalized")
        with self._lock:
            record = self.append(
                run_id=self.run_id, category="strategy",
                entity_type="strategy_intent", entity_id=intent.intent_id,
                account_id=account_id, event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": strategy_id,
                         "strategy_revision": strategy_revision})
            self._strategy_one_protection[record.record_id] = intent
            return record

    def strategy_one_protection_for_record(self, record_id: str) -> Any | None:
        with self._lock:
            return self._strategy_one_protection.get(record_id)

    def append_numbered_session_exit_intent(self, *, intent, account_id, strategy_id,
                                           strategy_revision):
        from src.trading_runtime.signals import StrategyIntent
        if (not isinstance(intent, StrategyIntent) or intent.action != "exit"
                or intent.reason != ("strategy_two_session_exit" if strategy_revision == 2 else "strategy_three_session_exit" if strategy_revision == 3 else "strategy_four_session_exit" if strategy_revision == 4 else "strategy_five_session_exit" if strategy_revision == 5 else "strategy_six_session_exit" if strategy_revision == 6 else "strategy_seven_session_exit" if strategy_revision == 7 else "strategy_eight_session_exit" if strategy_revision == 8 else "strategy_nine_session_exit" if strategy_revision == 9 else "strategy_ten_session_exit" if strategy_revision == 10 else "strategy_eleven_session_exit" if strategy_revision == 11 else "strategy_twelve_session_exit") or intent.metadata
                or strategy_id != "early-squeeze-strategy" or strategy_revision not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
                or not account_id):
            raise ValueError("Session exit requires Strategy 2 normalized scalar source")
        with self._lock:
            record = self.append(run_id=self.run_id, category="strategy",
                entity_type="strategy_intent", entity_id=intent.intent_id,
                account_id=account_id, event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": strategy_id,
                         "strategy_revision": strategy_revision})
            self._numbered_session_exits[record.record_id] = intent
            return record

    def numbered_session_exit_for_record(self, record_id):
        with self._lock:
            return self._numbered_session_exits.get(record_id)

    def append_followthrough_exit(self, *, intent, witness, source_entry_intent_id,
                                 account_id, strategy_id, strategy_revision, assignment_id=None):
        from src.trading_runtime.arte_followthrough_failure_v4 import REASON, validate_numbered_failure
        from uuid import UUID
        validate_numbered_failure(witness, strategy_revision)
        UUID(source_entry_intent_id)
        if (type(strategy_revision) is not int or strategy_revision not in (9, 10, 11, 12) or strategy_id != "early-squeeze-strategy"
                or not account_id or intent.action != "exit" or intent.reason != REASON
                or intent.metadata or intent.reference_price != witness.bid):
            raise ValueError("Follow-through exit lacks exact numbered scalar authority")
        with self._lock:
            source = (intent, witness, source_entry_intent_id)
            if assignment_id is not None:
                if not isinstance(assignment_id, str) or not assignment_id:
                    raise ValueError("Failure source assignment must be exact")
                prior_assignment = self._entry_assignments.get(source_entry_intent_id)
                if prior_assignment is not None and prior_assignment != assignment_id:
                    raise ValueError("Failure source changed original entry assignment")
                self._entry_assignments[source_entry_intent_id] = assignment_id
            prior_intent = self._followthrough_intents.get(intent.intent_id)
            if prior_intent is not None:
                prior_record, prior_source = prior_intent
                if (prior_source != source or prior_record.account_id != account_id
                        or prior_record.payload.get("strategy_revision") != strategy_revision):
                    raise ValueError("Follow-through retry changed immutable witness")
                return prior_record
            record = self.append(run_id=self.run_id, category="strategy",
                entity_type="strategy_intent", entity_id=intent.intent_id,
                account_id=account_id, event_time=intent.event_time,
                payload={**intent.payload(), "strategy_id": strategy_id,
                         "strategy_revision": strategy_revision})
            self._followthrough_intents[intent.intent_id] = (record, source)
            prior = self._followthrough_exits.get(record.record_id)
            if prior is not None and prior != source:
                raise ValueError("Follow-through retry changed immutable witness")
            self._followthrough_exits[record.record_id] = source
            return record

    def assignment_for_intent(self, intent_id):
        with self._lock:
            return self._entry_assignments.get(intent_id)

    def followthrough_exit_for_record(self, record_id):
        with self._lock:
            return self._followthrough_exits.get(record_id)

    def append_strategy_order_command(
        self, *, order_request: Any, run_id: str, category: str,
        entity_type: str, entity_id: str, payload: dict[str, Any],
        account_id: str, event_time: datetime,
    ) -> JournalRecord:
        """Retain the original typed order until its normalized command is fenced."""
        from src.trading_runtime.ibkr_schema import OrderRequest

        if (not isinstance(order_request, OrderRequest)
                or run_id != self.run_id or category != "command"
                or entity_type != "order" or not order_request.raw
                or order_request.acctId != account_id
                or order_request.cOID != entity_id
                or not payload.get("strategy_intent_id")
                or not payload.get("order_group_id")
                or any(payload.get(key) != value
                       for key, value in order_request.to_cpapi().items())):
            raise ValueError("Strategy order command lacks its original typed request")
        with self._lock:
            record = self.append(
                run_id=run_id, category=category, entity_type=entity_type,
                entity_id=entity_id, payload=payload, account_id=account_id,
                event_time=event_time)
            self._order_requests[record.record_id] = deepcopy(order_request)
            return record

    def order_request_for_record(self, record_id: str) -> Any | None:
        with self._lock:
            return self._order_requests.get(record_id)

    def append_oms_group_transition(
        self, *, group: Any, run_id: str, category: str, entity_type: str,
        entity_id: str, payload: dict[str, Any], account_id: str,
        event_time: datetime,
    ) -> JournalRecord:
        """Atomically pair a transition with its immutable projection snapshot."""
        from src.trading_runtime.arte_oms_projection import freeze_oms_group

        if (run_id != self.run_id or category != "order_management"
                or entity_type != "order_group_state"
                or group.group_id != entity_id or group.account_id != account_id
                or group.updated_at != event_time):
            raise ValueError("OMS transition and snapshot identities differ")
        frozen = freeze_oms_group(group)
        with self._lock:
            reservation_id = str(group.intent.metadata.get("portfolio_reservation_id") or "")
            admission = (self.portfolio_admission_reservation(account_id, reservation_id)
                         if reservation_id else None)
            if reservation_id and (admission is None
                                   or admission.get("intent_id") != group.intent.intent_id):
                raise ValueError("OMS transition lacks its Portfolio reservation")
            record = self.append(
                run_id=run_id, category=category, entity_type=entity_type,
                entity_id=entity_id, payload=payload, account_id=account_id,
                event_time=event_time)
            self._oms_groups[record.record_id] = frozen
            self._oms_admissions[record.record_id] = deepcopy(admission)
            return record

    def oms_group_for_record(self, record_id: str) -> Any | None:
        with self._lock:
            return self._oms_groups.get(record_id)

    def oms_admission_for_record(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._oms_admissions.get(record_id))

    def oms_effective_protection_for_record(
        self, record: JournalRecord,
    ) -> dict[str, JournalRecord]:
        """Earlier normalized effective amendments only; never use a future fact."""
        with self._lock:
            if (record.run_id != self.run_id or record.category != "order_management"
                    or record.entity_type != "order_group_state"):
                raise ValueError("OMS protection lookup requires a group transition")
            result: dict[str, JournalRecord] = {}
            key = (record.entity_id, record.payload.get("intent_id"),
                   record.account_id)
            try:
                hash(key)
            except TypeError:
                return {}
            matching = self._effective_protection_records.get(key, ())
            end = bisect_left(matching, record.sequence,
                              key=lambda prior: prior.sequence)
            for index in range(end - 1, -1, -1):
                prior = matching[index]
                payload = prior.payload
                kind = payload.get("kind")
                if (kind in {"stop", "target"}
                        and payload.get("phase") == "effective"
                        and payload.get("order_group_id") == record.entity_id
                        and payload.get("source_intent_id") == record.payload.get("intent_id")
                        and prior.account_id == record.account_id
                        and payload.get("action") in {
                            "replace_protective_stop", "replace_profit_target"}):
                    result.setdefault(kind, prior)
                    client_order_id = payload.get("client_order_id")
                    if isinstance(client_order_id, str) and client_order_id:
                        result.setdefault(f"{kind}:{client_order_id}", prior)
            return result

    def append_many(self, entries: Iterable[dict[str, Any]]) -> list[JournalRecord]:
        pending = [dict(entry) for entry in entries]
        with self._lock:
            self._require_open()
            if self._next_sequence - self._fenced_sequence + len(pending) > self.max_pending_records:
                raise RuntimeError("Backtest journal buffer is full; durable fence is required")
            result: list[JournalRecord] = []
            for entry in pending:
                run_id = str(entry["run_id"])
                if run_id != self.run_id:
                    raise ValueError("Backtest journal cannot mix runs")
                category = str(entry["category"])
                entity_type = str(entry["entity_type"])
                entity_id = str(entry["entity_id"])
                at = entry.get("event_time") or datetime.now(timezone.utc)
                if not isinstance(at, datetime) or at.tzinfo is None:
                    raise ValueError("Backtest journal event time must be timezone-aware")
                at = at.astimezone(timezone.utc)
                # The caller retains ownership of its observation. Snapshot
                # nested evidence before an asynchronous typed projector sees it.
                payload = deepcopy(dict(entry.get("payload") or {}))
                active_lineage = current_request_identity()
                needs_lineage = not payload.get("intent_id") or bool(active_lineage)
                lineage = (causal_identity(correlation_seed=run_id,
                    causation_seed=f"{category}:{entity_type}:{entity_id}:"
                                   f"{at.isoformat(timespec='microseconds')}")
                    if needs_lineage else {})
                payload = {**lineage, **payload}
                # The command path owns only a defensive snapshot. The typed
                # publication worker validates/serializes its bounded prefix;
                # JSON encoding here would put evidence-sized CPU work on
                # every simulated market boundary.
                record = JournalRecord(
                    record_id=str(uuid4()), run_id=run_id,
                    sequence=self._next_sequence + len(result) + 1,
                    event_time=at, recorded_at=datetime.now(timezone.utc),
                    category=category, entity_type=entity_type,
                    entity_id=entity_id,
                    account_id=str(entry.get("account_id") or ""), payload=payload,
                )
                result.append(record)
            creations: dict[tuple[str, str], dict[str, Any]] = {}
            for record in result:
                if (record.category == "portfolio_management"
                        and record.entity_type == "portfolio_reservation"
                        and record.payload.get("event") == "reservation_created"):
                    key = (record.account_id, record.entity_id)
                    if (not all(key)
                            or record.payload.get("reservation_id") != record.entity_id
                            or record.payload.get("status") != "reserved"
                            or key in self._reservation_creations or key in creations):
                        raise ValueError("Portfolio reservation creation is invalid or duplicated")
                    creations[key] = deepcopy(record.payload)
            self._records.extend(result)
            self._next_sequence += len(result)
            self._reservation_creations.update(creations)
            for record in result:
                if record.category == "market_discovery_signal":
                    self._live_counts["signals"] += 1
                elif record.category == "strategy" and record.entity_type == "strategy_intent":
                    self._live_counts["intents"] += 1
                elif record.category == "command" and record.entity_type == "order":
                    self._live_counts["commands"] += 1
                elif record.category == "execution" and record.entity_type == "fill":
                    self._live_counts["fills"] += 1
                if record.category == "market_discovery_signal":
                    self._signal_records.append(record)
                    self._by_identity.setdefault(
                        (record.category, record.entity_type, record.entity_id), record)
                if record.category == "protection":
                    self._protection_records.append(record)
                    self._index_effective_protection(record)
            return result

    def _index_effective_protection(self, record: JournalRecord) -> None:
        """Register one immutable protection fact in sequence order."""
        payload = record.payload
        if (payload.get("kind") not in {"stop", "target"}
                or payload.get("phase") != "effective"
                or payload.get("action") not in {
                    "replace_protective_stop", "replace_profit_target"}):
            return
        key = (payload.get("order_group_id"), payload.get("source_intent_id"),
               record.account_id)
        try:
            hash(key)
        except TypeError:
            return
        self._effective_protection_records.setdefault(key, []).append(record)

    @property
    def live_counts(self) -> dict[str, int]:
        """Provisional, in-process activity totals; never durability evidence."""
        with self._lock:
            return dict(self._live_counts)

    def unfenced_records(self, *, after_sequence: int | None = None,
                         through_sequence: int | None = None) -> list[JournalRecord]:
        """Return an immutable-to-the-caller prefix for asynchronous publication."""
        with self._lock:
            start = self._fenced_sequence if after_sequence is None else int(after_sequence)
            if start < self._fenced_sequence or start > self._next_sequence:
                raise ValueError("Journal publication cursor is outside the unfenced prefix")
            end = self._next_sequence if through_sequence is None else through_sequence
            if type(end) is not int or end < start or end > self._next_sequence:
                raise ValueError("Journal publication limit is outside the unfenced prefix")
            return list(self._records[start - self._base_sequence:
                                      end - self._base_sequence])

    @property
    def pending_record_count(self) -> int:
        with self._lock:
            return self._next_sequence - self._fenced_sequence

    def mark_fenced(self, sequence: int) -> None:
        """Release pending capacity only after ClickHouse confirms its fence."""
        with self._lock:
            if not self._fenced_sequence <= sequence <= self._next_sequence:
                raise ValueError("Journal fence sequence is outside the current prefix")
            discard = sequence - self._base_sequence
            if discard:
                for record in self._records[:discard]:
                    self._strategy_one_entries.pop(record.record_id, None)
                    self._strategy_one_adds.pop(record.record_id, None)
                    self._strategy_one_protection.pop(record.record_id, None)
                    self._numbered_session_exits.pop(record.record_id, None)
                    self._followthrough_exits.pop(record.record_id, None)
                    self._oms_groups.pop(record.record_id, None)
                    self._oms_admissions.pop(record.record_id, None)
                    self._order_requests.pop(record.record_id, None)
                    self._backtest_progress.pop(record.record_id, None)
                del self._records[:discard]
                self._base_sequence = sequence
            self._fenced_sequence = sequence

    def append_once(self, *, run_id: str, category: str, entity_type: str,
                    entity_id: str, payload: dict[str, Any], account_id: str = "",
                    event_time: datetime | None = None) -> tuple[JournalRecord, bool]:
        return self.append_once_many([dict(run_id=run_id, category=category,
            entity_type=entity_type, entity_id=entity_id, payload=payload,
            account_id=account_id, event_time=event_time)])[0]

    def append_once_many(self, entries: Iterable[dict[str, Any]]) -> list[tuple[JournalRecord, bool]]:
        pending = [dict(entry) for entry in entries]
        keys = [(str(entry["category"]), str(entry["entity_type"]),
                 str(entry["entity_id"])) for entry in pending]
        with self._lock:
            new = {}
            for entry, key in zip(pending, keys, strict=True):
                if key not in self._by_identity and key not in new:
                    new[key] = entry
            inserted = dict(zip(new, self.append_many(new.values()), strict=True))
            self._by_identity.update(inserted)
            result = []
            emitted = set()
            for key in keys:
                if key in inserted:
                    result.append((inserted[key], key not in emitted))
                    emitted.add(key)
                else:
                    result.append((self._by_identity[key], False))
            return result

    def records(self, run_id: str, *, after_sequence: int = 0) -> list[JournalRecord]:
        if run_id != self.run_id:
            return []
        with self._lock:
            start = max(0, int(after_sequence))
            if start < self._base_sequence:
                raise ValueError("Fenced Backtest records must be read from ClickHouse")
            return list(self._records[start - self._base_sequence:])

    def latest_sequence(self, run_id: str) -> int:
        return self._next_sequence if run_id == self.run_id else 0

    def next_record_after_time(self, run_id: str, event_time: datetime, *,
                               categories: tuple[str, ...]) -> JournalRecord | None:
        if run_id != self.run_id or not categories:
            return None
        with self._lock:
            # Avoid materializing two potentially long record histories for a
            # read-only navigation lookup. Signals may also still be pending in
            # _records; retaining the existing min-by-time semantics is safe.
            matches = (record for record in chain(self._signal_records, self._records)
                       if record.category in categories and record.event_time > event_time)
            return min(matches, key=lambda record: (record.event_time, record.sequence),
                       default=None)

    def protection_records(self, run_id: str, after_sequence: int = 0) -> list[JournalRecord]:
        if run_id != self.run_id:
            return []
        # These records are appended (and restored) in sequence order. Runtime
        # snapshots poll only the new suffix; searching the full historical
        # protection journal on every snapshot becomes quadratic over a run.
        with self._lock:
            start = bisect_right(self._protection_records, after_sequence,
                                 key=lambda record: record.sequence)
            return self._protection_records[start:]

    def save_checkpoint(self, run_id: str, cursor: str,
                        state: dict[str, Any], event_time: datetime) -> None:
        if run_id != self.run_id or event_time.tzinfo is None:
            raise ValueError("Backtest checkpoint identity or time is invalid")
        canonical_json(state)
        self._checkpoint = dict(run_id=run_id, cursor=cursor,
            event_time=event_time.astimezone(timezone.utc).isoformat(),
            state=dict(state), updated_at=datetime.now(timezone.utc).isoformat())

    def load_checkpoint(self, run_id: str, *, immutable: bool = False) -> dict[str, Any] | None:
        if immutable:
            raise ValueError("Immutable Backtest review must read a fenced ClickHouse checkpoint")
        return dict(self._checkpoint) if run_id == self.run_id and self._checkpoint else None

    def save_portfolio_state(self, account_id: str, state: dict[str, Any]) -> None:
        if not account_id:
            raise ValueError("account_id is required")
        canonical_json(state)
        self._portfolio_states[account_id] = dict(state)

    def portfolio_states(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._portfolio_states.items()}

    def command_checkpoint(self) -> dict[str, Any]:
        """Compact mutable command state not derivable from broker/strategy state.

        Journal events and their evidence remain in ClickHouse. Assignments
        already live in the controller checkpoint and are republished into
        this adapter when the strategy runtime is restored.
        """
        with self._lock:
            state = {
                "schema_version": 1,
                "run_id": self.run_id,
                "portfolio_states": deepcopy(self._portfolio_states),
                "order_states": deepcopy(self._order_states),
                "leases": deepcopy(self._leases),
                "campaign_ownership": [deepcopy(self._campaign_ownership[key])
                                       for key in sorted(self._campaign_ownership)],
            }
            canonical_json(state)
            return state

    def restore_command_checkpoint(self, state: dict[str, Any]) -> None:
        """Restore only a verified fenced command snapshot before runtime init."""
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or state.get("run_id") != self.run_id):
            raise ValueError("Backtest journal command checkpoint identity changed")
        names = ("portfolio_states", "order_states", "leases")
        if any(not isinstance(state.get(name), dict) for name in names):
            raise ValueError("Backtest journal command checkpoint omitted state maps")
        if any(not isinstance(row, dict) for name in names
               for row in state[name].values()):
            raise ValueError("Backtest journal command checkpoint contains malformed rows")
        campaigns = state.get("campaign_ownership")
        if not isinstance(campaigns, list):
            raise ValueError("Backtest journal command checkpoint omitted campaign ownership")
        restored_campaigns: dict[tuple[str, str], dict[str, Any]] = {}
        for row in campaigns:
            if not isinstance(row, dict):
                raise ValueError("Backtest journal campaign ownership is malformed")
            key = (str(row.get("resource_id") or ""), str(row.get("session_key") or ""))
            if not all(key) or key in restored_campaigns:
                raise ValueError("Backtest journal campaign ownership is duplicated")
            restored_campaigns[key] = deepcopy(row)
        if any(str(row.get("run_id") or "") != self.run_id
               for row in state["order_states"].values()):
            raise ValueError("Backtest journal order checkpoint mixed runs")
        canonical_json(state)
        with self._lock:
            self._require_open()
            if (self._records or self._portfolio_states or self._order_states
                    or self._leases or self._campaign_ownership):
                raise ValueError("Backtest journal command state was already initialized")
            self._portfolio_states = deepcopy(state["portfolio_states"])
            self._order_states = deepcopy(state["order_states"])
            self._leases = deepcopy(state["leases"])
            self._campaign_ownership = restored_campaigns

    def restore_committed_records(self, records: Iterable[JournalRecord]) -> None:
        """Index only fenced signals/protections needed by resumed command logic."""
        restored = list(records)
        if (any(record.run_id != self.run_id
                or record.sequence > self._fenced_sequence
                or record.sequence < 1
                or record.category not in {"market_discovery_signal", "protection"}
                for record in restored)
                or any(left.sequence >= right.sequence
                       for left, right in zip(restored, restored[1:]))):
            raise ValueError("Backtest journal committed record prefix is invalid")
        with self._lock:
            self._require_open()
            if self._records or self._signal_records or self._protection_records:
                raise ValueError("Backtest journal committed records were already restored")
            for record in restored:
                if record.category == "market_discovery_signal":
                    self._signal_records.append(record)
                    self._by_identity.setdefault(
                        (record.category, record.entity_type, record.entity_id), record)
                else:
                    self._protection_records.append(record)
                    self._index_effective_protection(record)

    def portfolio_reservation(self, account_id: str, reservation_id: str) -> dict[str, Any] | None:
        for reservation in self._portfolio_states.get(account_id, {}).get("reservations") or ():
            if str(reservation.get("reservation_id") or "") == reservation_id:
                return dict(reservation)
        return None

    def portfolio_admission_reservation(
        self, account_id: str, reservation_id: str,
    ) -> dict[str, Any] | None:
        """Original normalized admission fact for OMS projection, not live sizing."""
        with self._lock:
            creation = self._reservation_creations.get((account_id, reservation_id))
            return deepcopy(creation) if creation is not None else None

    def restore_verified_portfolio_admissions(self, lineages: Any) -> None:
        """Seed only committed OMS admission facts from a cold typed join."""
        from src.trading_runtime.arte_oms_projection import (
            RecoveredStrategyOneOmsLineage,
        )

        restored: dict[tuple[str, str], dict[str, Any]] = {}
        for lineage in lineages:
            if (not isinstance(lineage, RecoveredStrategyOneOmsLineage)
                    or lineage.approved_intent is None
                    or not isinstance(lineage.admission_reservation, Mapping)):
                raise ValueError("Cold OMS admission lacks verified normalized rows")
            row = dict(lineage.admission_reservation)
            key = (str(row.get("account_id") or ""),
                   str(row.get("reservation_id") or ""))
            metadata = lineage.approved_intent.metadata
            if (not all(key)
                    or row.get("event") != "reservation_created"
                    or row.get("status") != "reserved"
                    or row.get("intent_id") != lineage.approved_intent.intent_id
                    or key[0] != lineage.state.group.get("account_id")
                    or key[1] != metadata.get("portfolio_reservation_id")
                    or row.get("assignment_id") != metadata.get("assignment_id")):
                raise ValueError("Cold OMS admission differs from its approved intent")
            previous = restored.setdefault(key, row)
            if previous != row:
                raise ValueError("Cold OMS admission repeats a conflicting reservation")
        with self._lock:
            self._require_open()
            if self._records or self._reservation_creations:
                raise ValueError("Cold OMS admission requires a clean fenced lane")
            self._reservation_creations = restored

    def acquire_portfolio_admission_lease(self, resource_id: str, *, owner_id: str,
                                          ttl_seconds: float = 30.0) -> dict[str, Any] | None:
        if not resource_id or not owner_id or ttl_seconds <= 0:
            raise ValueError("Portfolio lease resource, owner, and positive TTL are required")
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = self._leases.get(resource_id)
            if (existing and datetime.fromisoformat(existing["expires_at"]) > now
                    and existing["owner_id"] != owner_id):
                return None
            lease = dict(resource_id=resource_id, owner_id=owner_id,
                epoch=int(existing["epoch"]) + 1 if existing else 1,
                expires_at=(now + timedelta(seconds=float(ttl_seconds))).isoformat())
            self._leases[resource_id] = lease
            return dict(lease)

    def portfolio_admission_lease_is_current(self, resource_id: str, *, owner_id: str,
                                             epoch: int) -> bool:
        lease = self._leases.get(resource_id)
        return bool(lease and lease["owner_id"] == owner_id
                    and int(lease["epoch"]) == int(epoch)
                    and datetime.fromisoformat(lease["expires_at"]) > datetime.now(timezone.utc))

    def release_portfolio_admission_lease(self, resource_id: str, *, owner_id: str,
                                          epoch: int) -> bool:
        with self._lock:
            lease = self._leases.get(resource_id)
            if lease and lease["owner_id"] == owner_id and int(lease["epoch"]) == int(epoch):
                del self._leases[resource_id]
                return True
            return False

    def acquire_campaign_session_ownership(self, resource_id: str, *, session_key: str,
                                           owner_id: str, state: str) -> dict[str, Any] | None:
        if not resource_id or not session_key or not owner_id:
            raise ValueError("Campaign ownership requires resource, session, and owner")
        if state not in {"reserved", "confirmed"}:
            raise ValueError("Campaign ownership state must be reserved or confirmed")
        key = (resource_id, session_key)
        with self._lock:
            self._require_open()
            prior = self._campaign_ownership.get(key)
            if prior is not None and prior["owner_id"] != owner_id:
                return None
            row = {
                "resource_id": resource_id, "session_key": session_key,
                "owner_id": owner_id,
                "state": "confirmed" if prior and prior["state"] == "confirmed" else state,
                "epoch": int(prior["epoch"]) + 1 if prior else 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._campaign_ownership[key] = row
            return {name: row[name] for name in ("resource_id", "session_key", "owner_id", "state", "epoch")}

    def campaign_session_ownership(self, resource_id: str, *, session_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._campaign_ownership.get((resource_id, session_key))
            return dict(row) if row is not None else None

    def campaign_ownership_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Capture only active owner scalars for a normalized V4 checkpoint.

        The mutation wall clock is deliberately excluded: campaign ownership
        affects future admission through identity, state, and epoch, while the
        checkpoint cursor supplies its causal completed-market boundary.
        """
        with self._lock:
            self._require_open()
            return tuple({name: row[name] for name in (
                "resource_id", "session_key", "owner_id", "state", "epoch")}
                for _, row in sorted(self._campaign_ownership.items()))

    def restore_verified_campaign_ownership(self, rows: Any) -> None:
        """Install one attested normalized owner image at its fenced cursor.

        The caller must load this image from ClickHouse and verify its Keeper
        head first. This adapter neither reads disk nor constructs missing
        owners; installation is atomic and can happen only on a clean lane.
        """
        from src.backend.backtest_market_data import market_day_boundary
        from src.trading_runtime.strategy_one_campaign_snapshot import (
            CampaignSnapshotRows, verify_campaign_snapshot,
        )

        if not isinstance(rows, CampaignSnapshotRows):
            raise TypeError("Campaign restore requires normalized rows")
        verified = verify_campaign_snapshot(rows)
        root = verified.snapshot
        if root["run_id"] != self.run_id:
            raise ValueError("Campaign restore belongs to another run")
        completed_at = market_day_boundary(
            root["session_date"], root["boundary_ms"]
        ).astimezone(timezone.utc).isoformat()
        restored = {
            (row["resource_id"], row["session_key"]): {
                name: row[name] for name in (
                    "resource_id", "session_key", "owner_id", "state", "epoch")
            } | {"updated_at": completed_at}
            for row in verified.owners
        }
        with self._lock:
            self._require_open()
            if (self._fenced_sequence != root["checkpoint_sequence"]
                    or self._records or self._campaign_ownership):
                raise ValueError("Campaign restore requires a clean fenced lane")
            self._campaign_ownership = restored

    def release_campaign_session_reservation(self, resource_id: str, *, session_key: str,
                                             owner_id: str) -> bool:
        key = (resource_id, session_key)
        with self._lock:
            row = self._campaign_ownership.get(key)
            if row is None or row["owner_id"] != owner_id or row["state"] != "reserved":
                return False
            del self._campaign_ownership[key]
            return True

    def save_order_management_state(self, group_id: str, *, run_id: str,
                                    account_id: str, state: dict[str, Any]) -> None:
        if not group_id or not account_id or run_id != self.run_id:
            raise ValueError("Order state identity is invalid")
        canonical_json(state)
        self._order_states[group_id] = dict(group_id=group_id, run_id=run_id,
            account_id=account_id, state=dict(state),
            updated_at=datetime.now(timezone.utc).isoformat())

    def order_management_states(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id and run_id != self.run_id:
            return []
        return sorted((dict(row) for row in self._order_states.values()),
                      key=lambda row: row["updated_at"])

    def save_strategy_assignment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.save_strategy_assignments([payload])[0]

    def save_strategy_assignments(self, payloads: Iterable[dict[str, Any]], *,
                                  return_rows: bool = True) -> list[dict[str, Any]]:
        prepared = []
        now = datetime.now(timezone.utc).isoformat()
        for raw in payloads:
            payload = dict(raw)
            assignment_id = str(payload.get("assignment_id") or "").strip()
            if not assignment_id:
                raise ValueError("assignment_id is required")
            row = {
                "assignment_id": assignment_id,
                "strategy_id": str(payload.get("strategy_id") or ""),
                "strategy_revision": int(payload.get("strategy_revision") or 0),
                "account_id": str(payload.get("account_id") or ""),
                "ticker": str(payload.get("ticker") or "").upper(),
                "conid": int(payload.get("conid") or 0),
                "status": str(payload.get("status") or ""),
                "permissions": deepcopy(payload.get("permissions") or {}),
                "parameters": deepcopy(payload.get("parameters") or {}),
                "state": deepcopy(payload.get("state") or {}),
                "source": str(payload.get("source") or "order_entry"),
                "created_at": str(payload.get("created_at") or now),
                "updated_at": str(payload.get("updated_at") or now),
            }
            canonical_json(row)
            prepared.append(row)
        with self._lock:
            self._require_open()
            for row in prepared:
                prior = self._assignments.get(row["assignment_id"])
                if prior is not None:
                    row = {**prior, **{name: row[name] for name in
                        ("status", "permissions", "parameters", "state", "updated_at")}}
                self._assignments[row["assignment_id"]] = row
            if not return_rows:
                return []
            return [deepcopy(self._assignments[row["assignment_id"]]) for row in prepared]

    def strategy_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._assignments.get(assignment_id)
            return deepcopy(row) if row is not None else None

    def strategy_assignments(self, *, account_id: str = "", ticker: str = "",
                             active_only: bool = False) -> list[dict[str, Any]]:
        rows = [row for row in self._assignments.values()
                if (not account_id or row["account_id"] == account_id)
                and (not ticker or row["ticker"] == ticker.upper())
                and (not active_only or row["status"] not in {"disabled", "completed", "error"})]
        return [deepcopy(row) for row in sorted(rows, key=lambda row: row["updated_at"], reverse=True)]

    def reference_json(self, value: Any) -> dict[str, str]:
        raise RuntimeError("Typed Backtest cannot externalize JSON evidence")

    def reference_evidence(self, value: Any) -> Any:
        canonical_json(value)
        return deepcopy(value)

    def flush(self) -> None:
        """A synchronous flush cannot claim ClickHouse durability."""
        self._require_open()
        raise RuntimeError("Backtest journal durability requires an explicit async fence")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._strategy_one_entries.clear()
            self._strategy_one_adds.clear()
            self._order_requests.clear()
            self._backtest_progress.clear()
            self._oms_groups.clear()
            self._oms_admissions.clear()
            self._reservation_creations.clear()


class BacktestJournalPublisher:
    """Retired V1 publisher; review readers remain for old runs only."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Retired bt_* Backtest publisher is not allowed")

"""Read only fence-certified Backtest activity from the ClickHouse journal."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from src.backend.backtest_journal_clickhouse import _literal, _rows, load_fenced_checkpoint
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.journal_evidence import activity_payload


_ACTIVITY = (
    "market_discovery_signal", "watchlist_membership", "strategy",
    "strategy_decision", "order_management",
)
_EVENT_CATEGORIES = {
    "signal": ("market_discovery_signal",),
    "watchlist": ("watchlist_membership",),
    "decision": ("strategy", "strategy_decision"),
    "campaign_state": ("strategy", "strategy_decision"),
    "order": ("order_management",),
}


def _consequential(record: JournalRecord) -> bool:
    category, entity, payload = record.category, record.entity_type, record.payload
    metadata = payload.get("metadata") or {}
    if category == "market_discovery_signal":
        return True
    if category == "strategy_decision" and (metadata.get("historical_hod_reference") or {}).get("changed") == 1:
        return True
    if category in {"strategy", "strategy_decision"} and entity != "strategy_assignment_state":
        if str(payload.get("action") or "").lower() not in {"", "wait"}:
            return True
    if category == "strategy_decision" and (metadata.get("continuation_detector") or {}).get("sequence") is not None:
        return True
    if entity == "strategy_assignment_state":
        state = payload.get("state") or {}
        active = state.get("active_stop")
        initial = state.get("initial_stop")
        if active is not None and (initial is None or abs(float(active) - float(initial)) > 1e-9):
            return True
    if category == "order_management":
        return bool(
            entity == "protection_reconciliation" and payload.get("actions")
            or entity == "order_group_state" and payload.get("event") == "profit_target_replaced"
            or entity == "entry_acquisition_frozen_before_exit"
        )
    return False


def _utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _record(row: dict[str, Any]) -> JournalRecord:
    return JournalRecord(
        record_id=str(row["record_id"]), run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        event_time=_utc_timestamp(row["event_time"]),
        recorded_at=_utc_timestamp(row["recorded_at"]),
        category=str(row["category"]), entity_type=str(row["entity_type"]),
        entity_id=str(row["entity_id"]), account_id=str(row["account_id"]),
        payload=json.loads(row["payload_json"]),
    )


class BacktestJournalReader:
    """A stable, verified committed prefix; staged events never reach review."""

    def __init__(self, client: Any, run_id: str, *,
                 fenced_sequence: int | None = None,
                 batch_ids: tuple[str, ...] | None = None) -> None:
        self.client = client
        self.run_id = str(UUID(run_id))
        if (fenced_sequence is None) != (batch_ids is None):
            raise ValueError("Backtest journal fence sequence and batches must be pinned together")
        if fenced_sequence is None:
            self.checkpoint = load_fenced_checkpoint(client, self.run_id)
            self.sequence = int(self.checkpoint["sequence"]) if self.checkpoint else 0
            self.batch_ids = tuple(self.checkpoint["batch_ids"]) if self.checkpoint else ()
        else:
            self.checkpoint = None
            self.sequence = int(fenced_sequence)
            self.batch_ids = tuple(str(UUID(value)) for value in batch_ids or ())
            if self.sequence < 0 or bool(self.sequence) != bool(self.batch_ids):
                raise ValueError("Backtest journal publisher fence is incomplete")
            if len(self.batch_ids) != len(set(self.batch_ids)):
                raise ValueError("Backtest journal publisher repeated a committed batch")
        self._blobs: dict[str, str] = {}

    def close(self) -> None:
        self.client.close()

    def _select_records(self, filters: list[str], *, limit: int,
                        offset: int = 0, ascending: bool = False,
                        sequence_order: bool = False) -> list[JournalRecord]:
        if not self.batch_ids:
            return []
        scope = [
            f"run_id=toUUID({_literal(self.run_id)})",
            "batch_id IN (" + ",".join(f"toUUID({_literal(value)})" for value in self.batch_ids) + ")",
            f"sequence<={self.sequence}",
            *filters,
        ]
        direction = "ASC" if ascending else "DESC"
        order = (f"sequence {direction}" if sequence_order else
                 f"event_time {direction},recorded_at {direction},sequence {direction}")
        rows = _rows(self.client,
            "SELECT toString(record_id) AS record_id,toString(run_id) AS run_id,"
            "sequence,event_time,recorded_at,category,entity_type,entity_id,"
            "account_id,payload_json FROM arte.bt_event_v1 WHERE "
            + " AND ".join(scope)
            + f" ORDER BY {order} "
            + f"LIMIT {max(1, int(limit))} OFFSET {max(0, int(offset))} FORMAT JSONEachRow")
        return [_record(row) for row in rows]

    def protection_records(self, run_id: str, after_sequence: int = 0) -> list[JournalRecord]:
        if str(UUID(run_id)) != self.run_id:
            return []
        return self._select_records([
            "category='protection'", f"sequence>{int(after_sequence)}",
        ], limit=max(1, self.sequence), ascending=True, sequence_order=True)

    def committed_command_records(self, *, page_size: int = 4096) -> list[JournalRecord]:
        """Read only the fenced signal/protection prefix needed for resume."""
        if not 1 <= page_size <= 8192:
            raise ValueError("Backtest command history page size is out of bounds")
        result: list[JournalRecord] = []
        after = 0
        while True:
            page = self._select_records([
                "category IN ('market_discovery_signal','protection')",
                f"sequence>{after}",
            ], limit=page_size, ascending=True, sequence_order=True)
            if not page:
                return result
            if any(record.sequence <= after for record in page):
                raise ValueError("Backtest committed command history is not ordered")
            result.extend(page)
            after = page[-1].sequence
            if len(page) < page_size:
                return result

    def signal_stream_records(
        self, *, run_id: str = "", signal_stream_id: str = "",
        from_time: datetime | None = None, as_of: datetime | None = None,
        limit: int = 10_000,
    ) -> list[JournalRecord]:
        if run_id and str(UUID(run_id)) != self.run_id:
            return []
        filters = ["category='market_discovery_signal'",
                   "entity_type='signal_occurrence'"]
        if signal_stream_id:
            filters.append("JSONExtractString(payload_json,'signal_stream_id')="
                           + _literal(signal_stream_id))
        for name, value, operator in (("from_time", from_time, ">="),
                                      ("as_of", as_of, "<=")):
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"Backtest journal {name} must be timezone-aware")
                filters.append("event_time" + operator + "parseDateTime64BestEffort("
                               + _literal(value.astimezone(timezone.utc).isoformat()) + ")")
        return self._select_records(filters, limit=min(max(1, int(limit)), 50_000))

    def _fetch_blob(self, digest: str) -> str | None:
        if digest in self._blobs:
            return self._blobs[digest]
        rows = _rows(self.client,
            "SELECT payload_json FROM arte.bt_blob_v1 "
            f"WHERE sha256={_literal(digest)} FORMAT JSONEachRow")
        values = {str(row["payload_json"]) for row in rows}
        if not values:
            return None
        if len(values) != 1:
            raise ValueError("Conflicting Backtest journal evidence blob")
        raw = values.pop()
        if sha256(raw.encode("utf-8")).hexdigest() != digest:
            raise ValueError("Corrupt Backtest journal evidence blob")
        self._blobs[digest] = raw
        return raw

    def strategy_activity_records(
        self, *, record_id: str = "", strategy_id: str = "", run_id: str = "",
        ticker: str = "", event_type: str = "", as_of: datetime | None = None,
        limit: int = 2000, offset: int = 0, consequential_only: bool = False,
        compact: bool = False, after_sequence: int = 0,
        through_sequence: int | None = None,
    ) -> list[JournalRecord]:
        if run_id and str(UUID(run_id)) != self.run_id:
            return []
        if event_type and event_type not in _EVENT_CATEGORIES:
            raise ValueError(f"Unknown Strategy Activity event type: {event_type}")
        if not self.batch_ids:
            return []
        maximum = min(self.sequence, through_sequence) if through_sequence is not None else self.sequence
        if maximum <= after_sequence:
            return []
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("Backtest journal as_of must be timezone-aware")
        categories = _EVENT_CATEGORIES.get(event_type, _ACTIVITY)
        filters = [
            f"run_id=toUUID({_literal(self.run_id)})",
            "batch_id IN (" + ",".join(f"toUUID({_literal(value)})" for value in self.batch_ids) + ")",
            f"sequence>{int(after_sequence)} AND sequence<={int(maximum)}",
            "category IN (" + ",".join(_literal(value) for value in categories) + ")",
        ]
        if record_id:
            filters.append(f"record_id=toUUID({_literal(str(UUID(record_id)))})")
        if strategy_id:
            filters.append(f"JSONExtractString(payload_json,'strategy_id')={_literal(strategy_id)}")
        if ticker:
            filters.append(f"upper(JSONExtractString(payload_json,'ticker'))={_literal(ticker.upper())}")
        if event_type == "decision":
            filters.append("entity_type!='strategy_assignment_state'")
        elif event_type == "campaign_state":
            filters.append("entity_type='strategy_assignment_state'")
        if as_of is not None:
            filters.append("event_time<=parseDateTime64BestEffort("
                           f"{_literal(as_of.astimezone(timezone.utc).isoformat())})")
        requested = max(1, min(int(limit), 50_001))
        skip = max(0, int(offset)) if consequential_only else 0
        scan_offset = 0 if consequential_only else max(0, int(offset))
        page_size = 4096 if consequential_only else requested
        result: list[JournalRecord] = []
        while len(result) < requested:
            rows = _rows(self.client,
                "SELECT toString(record_id) AS record_id,toString(run_id) AS run_id,"
                "sequence,event_time,recorded_at,category,entity_type,entity_id,"
                "account_id,payload_json FROM arte.bt_event_v1 WHERE "
                + " AND ".join(filters)
                + " ORDER BY event_time DESC,recorded_at DESC,sequence DESC "
                + f"LIMIT {page_size} OFFSET {scan_offset} FORMAT JSONEachRow")
            for row in rows:
                record = _record(row)
                payload = record.payload
                if consequential_only and not _consequential(record):
                    continue
                if skip:
                    skip -= 1
                    continue
                if compact:
                    record = replace(record, payload=activity_payload(payload, self._fetch_blob))
                result.append(record)
                if len(result) == requested:
                    break
            if len(rows) < page_size or not consequential_only:
                break
            scan_offset += len(rows)
        return result

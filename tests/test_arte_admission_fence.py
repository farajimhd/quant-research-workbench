from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import re

import pytest

from src.trading_runtime import arte_admission_fence as admission
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, _wire_row, typed_row
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot


RUN = "live:DU1:2026-08-18"
ATTEMPT = "00000000-0000-0000-0000-000000000011"
BATCH = "00000000-0000-0000-0000-000000000012"
RECORD = "00000000-0000-0000-0000-000000000013"
ZERO = "00000000-0000-0000-0000-000000000000"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
SNAPSHOT_HASH = "a" * 64


def batch() -> TypedJournalBatch:
    event = typed_row("trading_event_v1", {
        "run_id": RUN, "event_month": "2026-08-01", "attempt_id": ATTEMPT,
        "batch_id": BATCH, "record_id": RECORD, "sequence": 1,
        "event_time": AT.isoformat(), "recorded_at": AT.isoformat(),
        "category": "run_state", "entity_type": "lifecycle", "entity_id": RUN,
        "account_id": "DU1", "correlation_id": "", "causation_id": "",
    })
    return TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 1, "admission-1", "running", (event,))


def captured() -> CapturedPortfolioSnapshot:
    return CapturedPortfolioSnapshot(
        RUN, "DU1", 1, AT, "primary", "enabled", "synchronized",
        "broker-snapshot", AT, "", 1000.0, None, None,
        (), (), (), (), (), (),
    )


class Client:
    def __init__(self) -> None:
        self.fences: list[dict] = []
        self.events: list[dict] = []
        self.snapshots: list[dict] = []
        self.order: list[str] = []
        self.snapshot_corrupt = False

    def insert(self, name: str, rows, token: str) -> None:
        assert name == "trading_admission_fence_v1"
        for row in rows:
            self.fences.append(_wire_row(name, row))
            self.order.append(row["phase"])

    def query(self, sql: str) -> list[dict]:
        if "FROM arte.trading_admission_fence_v1" in sql:
            if "GROUP BY" in sql:
                if "ORDER BY account_id,state_revision" in sql:
                    keys = sorted({(row["account_id"], row["state_revision"])
                                   for row in self.fences})
                    after = re.search(r"AND \(account_id,state_revision\) > \('([^']+)',(\d+)\)", sql)
                    if after:
                        keys = [key for key in keys if key > (after.group(1), int(after.group(2)))]
                    limit = int(re.search(r"LIMIT (\d+)", sql).group(1))
                    return [{"account_id": account_id, "state_revision": revision}
                            for account_id, revision in keys[:limit]]
                if "latest_revision" in sql:
                    accounts = {}
                    for row in self.fences:
                        accounts[row["account_id"]] = max(
                            row["state_revision"], accounts.get(row["account_id"], 0))
                    return [{"account_id": account_id, "latest_revision": revision}
                            for account_id, revision in accounts.items()]
                groups = {}
                for row in self.fences:
                    groups.setdefault((row["account_id"], row["state_revision"]), []).append(row)
                return [{"account_id": account_id, "state_revision": revision,
                         "prepared_count": sum(r["phase"] == "prepared" for r in rows),
                         "committed_count": sum(r["phase"] == "committed" for r in rows),
                         "total_count": len(rows)}
                        for (account_id, revision), rows in groups.items()
                        if len(rows) != 2 or {r["phase"] for r in rows} != {"prepared", "committed"}][:1]
            revision = int(re.search(r"state_revision=(\d+)", sql).group(1))
            return [row for row in self.fences if row["state_revision"] == revision]
        if "FROM arte.trading_commit_v1" in sql:
            return list(self.events)
        if "FROM arte.trading_portfolio_snapshot_commit_v1" in sql:
            return list(self.snapshots)
        raise AssertionError(sql)


def install_fakes(monkeypatch, client: Client, *, fail_snapshot: bool = False) -> None:
    monkeypatch.setattr(admission, "_insert", lambda _client, name, rows, token:
                        client.insert(name, rows, token))
    monkeypatch.setattr(admission, "_rows", lambda _client, sql: client.query(sql))

    def publish_events(_client, item):
        if not client.events:
            client.events.append({"attempt_id": item.attempt_id,
                                  "first_sequence": item.first_sequence,
                                  "last_sequence": item.last_sequence})
            client.order.append("events")
        return item.batch_id

    def publish_snapshot(_client, prepared):
        if fail_snapshot:
            raise OSError("snapshot unavailable")
        if not client.snapshots:
            client.snapshots.append({"state_hash": SNAPSHOT_HASH})
            client.order.append("snapshot")
        return SNAPSHOT_HASH

    monkeypatch.setattr(admission, "publish_typed_batch", publish_events)
    monkeypatch.setattr(admission, "publish_prepared_portfolio_snapshot", publish_snapshot)

    def load_snapshot(_client, **identity):
        assert identity == {"run_id": RUN, "account_id": "DU1", "state_revision": 1}
        if client.snapshot_corrupt:
            raise RuntimeError("Portfolio snapshot content differs from its fence")
        return {"state_hash": client.snapshots[0]["state_hash"]} if client.snapshots else None

    monkeypatch.setattr(admission, "load_portfolio_snapshot", load_snapshot)


def test_persistent_admission_fence_commits_last_and_recovers() -> None:
    client = Client()
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client)
        assert admission.publish_fenced_admission(client, batch(), captured()) == SNAPSHOT_HASH
        assert client.order == ["prepared", "events", "snapshot", "committed"]
        loaded = admission.load_fenced_admission(
            client, run_id=RUN, account_id="DU1", state_revision=1)
        assert loaded is not None and loaded["snapshot_hash"] == SNAPSHOT_HASH
        admission.verify_no_incomplete_admissions(client, RUN)
        assert admission.publish_fenced_admission(client, batch(), captured()) == SNAPSHOT_HASH
        assert client.order == ["prepared", "events", "snapshot", "committed"]


def test_crash_between_event_and_snapshot_is_detected_and_retryable() -> None:
    client = Client()
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client, fail_snapshot=True)
        with pytest.raises(OSError, match="snapshot unavailable"):
            admission.publish_fenced_admission(client, batch(), captured())
        assert client.order == ["prepared", "events"]
        with pytest.raises(RuntimeError, match="prepared but not committed"):
            admission.load_fenced_admission(
                client, run_id=RUN, account_id="DU1", state_revision=1)
        with pytest.raises(RuntimeError, match="incomplete"):
            admission.verify_no_incomplete_admissions(client, RUN)
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client)
        admission.publish_fenced_admission(client, batch(), captured())
        assert client.order == ["prepared", "events", "snapshot", "committed"]


def test_retry_cannot_reuse_revision_with_changed_causal_identity() -> None:
    client = Client()
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client)
        admission.publish_fenced_admission(client, batch(), captured())
        altered = replace(captured(), snapshot_at=AT + timedelta(seconds=1))
        with pytest.raises(RuntimeError, match="conflicting content"):
            admission.publish_fenced_admission(client, batch(), altered)
        assert client.order == ["prepared", "events", "snapshot", "committed"]


def test_writer_startup_checks_for_prepared_only_admissions(monkeypatch) -> None:
    checked = []
    monkeypatch.setattr(writer, "_rows", lambda _client, _sql: [{"run_id": RUN}])
    monkeypatch.setattr(writer, "load_typed_run_context", lambda _client, _run_id: {})
    monkeypatch.setattr(admission, "verify_no_incomplete_admissions",
                        lambda _client, run_id: checked.append(run_id))
    writer._verify_run_identity(object(), RUN)
    assert checked == [RUN]


def test_startup_rejects_missing_latest_recovery_commit() -> None:
    client = Client()
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client)
        admission.publish_fenced_admission(client, batch(), captured())
        client.snapshots.clear()
        with pytest.raises(RuntimeError, match="missing or conflicting commits"):
            admission.verify_no_incomplete_admissions(client, RUN)


def test_startup_rejects_corrupt_snapshot_beneath_matching_commit() -> None:
    client = Client()
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client)
        admission.publish_fenced_admission(client, batch(), captured())
        client.snapshot_corrupt = True
        with pytest.raises(RuntimeError, match="content differs from its fence"):
            admission.verify_no_incomplete_admissions(client, RUN)


def test_startup_audits_older_committed_revision_not_only_latest() -> None:
    client = Client()
    with pytest.MonkeyPatch.context() as patch:
        install_fakes(patch, client)
        admission.publish_fenced_admission(client, batch(), captured())
        # A later complete revision is present, but the older one has lost its
        # backing event/snapshot commits. Startup must still reject the run.
        for row in tuple(client.fences):
            client.fences.append(dict(row, state_revision=2))
        seen = []
        def verify(_client, *, run_id, account_id, state_revision):
            seen.append(state_revision)
            if state_revision == 1:
                raise RuntimeError("Admission fence references missing or conflicting commits")
            return {"state_revision": state_revision}
        patch.setattr(admission, "load_fenced_admission", verify)
        with pytest.raises(RuntimeError, match="missing or conflicting commits"):
            admission.verify_no_incomplete_admissions(client, RUN, page_size=1)
        assert seen == [1]
        seen.clear()
        patch.setattr(admission, "load_fenced_admission", lambda _client, **identity:
                      seen.append(identity["state_revision"]) or identity)
        admission.verify_no_incomplete_admissions(client, RUN, page_size=1)
        assert seen == [1, 2]


def test_admission_fence_rejects_non_hash_snapshot_reference() -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        admission._fence(batch(), captured(), "committed", "not-a-hash")

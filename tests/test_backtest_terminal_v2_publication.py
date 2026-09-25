import json

import pytest

from src.backend import backtest_terminal_v2_publication as publication
from tests.test_backtest_terminal_v2_fence import (
    AT, ATTEMPT, BATCH, RUN, _suffix,
)
from src.backend import backtest_terminal_v2_publication as publication
from src.trading_runtime.arte_journal_writer import _canonical_typed_content


class FakeStorage:
    def __init__(self):
        self.tables = {name: [] for name in (
            "trading_event_v1", "trading_run_transition_v1",
            "trading_backtest_account_snapshot_v2",
            "trading_backtest_position_snapshot_v2",
            "trading_backtest_terminal_commit_v2",
        )}
        self.insert_order = []
        self.fail_commit_once = False

    def execute(self, sql):
        if sql.startswith("SELECT * FROM arte."):
            table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            return "\n".join(json.dumps(row) for row in self.tables[table])
        assert sql.startswith("INSERT INTO arte.")
        table = sql.split("INTO arte.", 1)[1].split(" ", 1)[0].split("(", 1)[0]
        if table == "trading_backtest_terminal_commit_v2" and self.fail_commit_once:
            self.fail_commit_once = False
            raise RuntimeError("fake crash before terminal seal")
        self.insert_order.append(table)
        self.tables[table].extend(json.loads(line) for line in
                                  sql.split("FORMAT JSONEachRow\n", 1)[1].splitlines())
        return ""


def _kwargs():
    prefix, events, transitions, accounts, positions = _suffix()
    return prefix, dict(
        account_ids=("DU1",), attempt_id=ATTEMPT, batch_id=BATCH,
        source_cursor="start", status="completed", committed_at=AT,
        events=events, transitions=transitions,
        accounts=accounts, positions=positions,
    )


def test_fake_publisher_commits_last_and_retries_exact_partial_facts(monkeypatch):
    prefix, kwargs = _kwargs()
    client = FakeStorage()
    monkeypatch.setattr(publication, "load_committed_prefix",
                        lambda _client, run_id, **kwargs: prefix if run_id == RUN and kwargs.get("journal_profile") == "backtest_v2" else None)
    client.fail_commit_once = True
    with pytest.raises(RuntimeError, match="fake crash"):
        publication.publish_terminal_v2_suffix(client, prefix, **kwargs)
    assert not client.tables["trading_backtest_terminal_commit_v2"]
    assert client.insert_order == [
        "trading_event_v1", "trading_run_transition_v1",
        "trading_backtest_account_snapshot_v2",
        "trading_backtest_position_snapshot_v2",
    ]
    seal = publication.publish_terminal_v2_suffix(client, prefix, **kwargs)
    assert client.insert_order[-1] == "trading_backtest_terminal_commit_v2"
    assert seal["last_sequence"] == 4
    assert len(client.tables["trading_event_v1"]) == 3
    assert publication.publish_terminal_v2_suffix(client, prefix, **kwargs) == seal
    assert len(client.insert_order) == 5
    monkeypatch.setattr(publication, "load_committed_prefix",
                        lambda _client, run_id, **kwargs: prefix if run_id == RUN and kwargs.get("journal_profile") == "backtest_v2" else None)
    assert publication.audit_terminal_v2_run(client, run_id=RUN,
                                              account_ids=("DU1",)) == seal


def test_fake_publisher_rejects_conflicting_uncommitted_fact_before_seal(monkeypatch):
    prefix, kwargs = _kwargs()
    client = FakeStorage()
    monkeypatch.setattr(publication, "load_committed_prefix",
                        lambda _client, run_id, **kwargs: prefix if run_id == RUN and kwargs.get("journal_profile") == "backtest_v2" else None)
    client.tables["trading_backtest_account_snapshot_v2"].append(
        {**kwargs["accounts"][0], "account_id": "OTHER"})
    with pytest.raises(ValueError, match="row hash differs"):
        publication.publish_terminal_v2_suffix(client, prefix, **kwargs)
    assert not client.tables["trading_backtest_terminal_commit_v2"]


def test_portfolio_anchor_is_durable_before_terminal_seal_and_retry_exact(monkeypatch):
    prefix, kwargs = _kwargs()
    client = FakeStorage()
    client.tables["trading_backtest_snapshot_anchor_v1"] = []
    # A captured worker input; normalization is separately tested.
    from types import SimpleNamespace
    capture = SimpleNamespace(run_id=RUN, account_id="DU1",
                              state_revision=4, snapshot_at=AT)
    monkeypatch.setattr(publication, "CapturedPortfolioSnapshot", type(capture))
    monkeypatch.setattr(publication, "prepare_captured_portfolio_snapshot",
                        lambda row: row)
    monkeypatch.setattr(publication, "load_committed_prefix", lambda *_, **__: prefix)
    def stored(_client, _run, account):
        return [_canonical_typed_content(
                    "trading_backtest_snapshot_anchor_v1",
                    {key: value for key, value in row.items() if key != "content_hash"})
                for row in client.tables["trading_backtest_snapshot_anchor_v1"]
                if row["account_id"] == account]
    monkeypatch.setattr(publication, "_stored_anchors", stored)
    monkeypatch.setattr(publication, "publish_prepared_portfolio_snapshot",
                        lambda *_: "a" * 64)
    monkeypatch.setattr(publication, "load_portfolio_snapshot",
                        lambda *_, **__: {"state_hash": "a" * 64})
    def insert(_client, table, rows, token):
        assert table == "trading_backtest_snapshot_anchor_v1"
        client.insert_order.append(table)
        client.tables[table].extend(rows)
    monkeypatch.setattr(publication, "_insert", insert)
    client.fail_commit_once = True
    with pytest.raises(RuntimeError, match="fake crash"):
        publication.publish_terminal_v2_suffix(
            client, prefix, **kwargs, portfolio_captures=(capture,))
    assert client.insert_order[-1] == "trading_backtest_snapshot_anchor_v1"
    assert not client.tables["trading_backtest_terminal_commit_v2"]
    publication.publish_terminal_v2_suffix(
        client, prefix, **kwargs, portfolio_captures=(capture,))
    assert client.insert_order[-1] == "trading_backtest_terminal_commit_v2"
    assert len(client.tables["trading_backtest_snapshot_anchor_v1"]) == 1

from types import SimpleNamespace
from pathlib import Path

import pytest

import scripts.clickhouse.publish_strategy_forty_three_v7 as campaign
from tests.test_strategy_forty_three_source import fixture


def setup(monkeypatch, tmp_path):
    _, arguments = fixture(monkeypatch)
    market = arguments["market"]
    credential = tmp_path / "reader.env"
    credential.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(campaign, "Path", lambda name: credential if str(name).endswith("read.env") else tmp_path)
    monkeypatch.setattr(campaign.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(campaign, "certified_market_plan_from_arte", lambda **_: market)
    monkeypatch.setattr(campaign, "project_market_day_plan", lambda plan, _: plan)
    monkeypatch.setattr(campaign, "canonical_stream_activation", lambda: ({}, {}))
    monkeypatch.setattr(campaign, "load_first_squeeze_occurrences", lambda *_a, **_k:
        {"occurrences": [{"ticker": "TEST", "last_price": 10.}]})
    monkeypatch.setattr(campaign, "certified_seed_plan", lambda *_: SimpleNamespace(token="c" * 64))
    monkeypatch.setattr(campaign, "verify_tables", lambda _: None)
    monkeypatch.setattr(campaign, "_credential", lambda **_: "private-test-value")
    monkeypatch.setattr(campaign, "_grant_set", lambda _: campaign._GRANTS)
    state = dict(inserts=[], deletes=[], certificates=[], derived=[])
    class Client:
        def execute(self, query):
            if query.startswith("SELECT currentUser"):
                return campaign.PRINCIPAL
            if query.startswith("SELECT ticker"):
                return ""
            state["inserts"].append(query)
            return ""
        def close(self):
            pass
    monkeypatch.setattr(campaign, "readonly_clickhouse_client", lambda **_: Client())
    monkeypatch.setattr(campaign, "_writer", lambda _: Client())
    def certify(*_args, **kwargs):
        state["certificates"].append(kwargs["candidate_tickers"])
        return SimpleNamespace(token="d" * 64, coverage=("TEST",))
    monkeypatch.setattr(campaign, "certify_v7_interval_plan", certify)
    keeper = SimpleNamespace(connected=True)
    keeper.stat = SimpleNamespace(ephemeralOwner=1, czxid=2, version=0)
    keeper.create = lambda *_a, **_k: None
    keeper.exists = lambda _: keeper.stat
    keeper.delete = lambda *args, **kwargs: state["deletes"].append((args, kwargs))
    owner = SimpleNamespace(client=keeper, close=lambda: None)
    monkeypatch.setattr(campaign, "open_workstation_keeper_session", lambda: owner)
    def derive(**kwargs):
        state["derived"].append(kwargs["ticker"])
        return SimpleNamespace(valid_seconds=(1000,), intervals=())
    monkeypatch.setattr(campaign, "derive_ticker_day", derive)
    def publish(writer, _reader, _item):
        writer.execute("INSERT INTO native_v7_derivative")
        return "published"
    monkeypatch.setattr(campaign, "publish_unit", publish)
    return market, state, keeper


def test_campaign_check_never_derives_or_writes_and_apply_seals_complete_scope(monkeypatch, tmp_path):
    market, state, _ = setup(monkeypatch, tmp_path)
    campaign.run(session=market.sessions[0], build=market.build_id, apply=False, workers=1)
    assert state["derived"] == state["inserts"] == state["certificates"] == []
    campaign.run(session=market.sessions[0], build=market.build_id, apply=True, workers=1)
    assert state["derived"] == ["TEST"]
    assert state["inserts"] == ["INSERT INTO native_v7_derivative"]
    assert state["certificates"] == [("TEST",)]
    assert len(state["deletes"]) == 1
    assert len(list(tmp_path.rglob("certificate.json"))) == 1


def test_campaign_lost_epoch_never_publishes_or_deletes_successor_lock(monkeypatch, tmp_path):
    market, state, keeper = setup(monkeypatch, tmp_path)
    def derive(**_):
        keeper.stat = SimpleNamespace(ephemeralOwner=3, czxid=4, version=0)
        return SimpleNamespace(valid_seconds=(1000,), intervals=())
    monkeypatch.setattr(campaign, "derive_ticker_day", derive)
    with pytest.raises(RuntimeError, match="failed 1 units"):
        campaign.run(session=market.sessions[0], build=market.build_id, apply=True, workers=1)
    assert state["inserts"] == state["deletes"] == state["certificates"] == []
    assert len(list(tmp_path.rglob("progress.json"))) == 1

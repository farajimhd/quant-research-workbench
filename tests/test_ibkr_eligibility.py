from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from services.reference_gateway import ibkr_eligibility as E
from services.reference_gateway.providers import IbkrReferenceClient
from src.backend.query_plans.market_tradable_universe_v1 import full_tradable_universe, tradable_symbol_lookup


@pytest.mark.parametrize("payload,expected", [
    ({"canTradeAcctIds": ["U1"]}, "eligible"),
    ({"canTradeAcctIds": ["U2"]}, "restricted"),
    ({"canTradeAcctIds": []}, "restricted"),
    ({}, "unknown"),
    ({"canTradeAcctIds": "U1"}, "unknown"),
    ({"canTradeAcctIds": [None]}, "unknown"),
    ({"canTradeAcctIds": ["U1"], "error": "unavailable"}, "unknown"),
    (None, "unknown"),
])
def test_classification(payload, expected):
    assert E.classify_rules(payload, "U1")[0] == expected


def test_account_selection_never_chooses_first_of_multiple():
    assert E.choose_account({"accounts": ["U1"]}, "") == "U1"
    assert E.choose_account({"accounts": ["U1", "U2"]}, "U2") == "U2"
    for payload, configured in [({"accounts": ["U1", "U2"]}, ""),
                                ({"accounts": ["U1"]}, "U2"), ({}, "")]:
        with pytest.raises(ValueError):
            E.choose_account(payload, configured)


@patch("services.reference_gateway.providers.fetch_json")
def test_web_rules_side_and_no_order_submission(fetch):
    fetch.return_value = {"canTradeAcctIds": ["U1"]}
    client = IbkrReferenceClient(base_url="https://localhost:5000/v1/api")
    for side in (True, False):
        client.contract_rules(123, is_buy=side)
        assert fetch.call_args.args[0].endswith("/iserver/contract/rules")
        assert fetch.call_args.kwargs["payload"] == {"conid": 123, "exchange": "SMART", "isBuy": side}


def test_failed_account_invalidates_previous_policy():
    client, broker = Mock(), Mock()
    broker.trading_accounts.side_effect = RuntimeError("private transport details")
    with patch.object(E, "ensure_schema"), patch.object(E, "insert") as insert:
        with pytest.raises(RuntimeError, match="live buy eligibility disabled"):
            E.sync_eligibility(client, "test", broker)
        record = insert.call_args.args[2][0]
        assert record["account_id"] == ""
        assert record["valid_until_utc"] == record["observed_at_utc"]


def test_dependency_preflight_can_invalidate_existing_policy_without_bootstrap():
    with patch.object(E,"rows",side_effect=[[{"storage_policy":"live_market_ssd"}],[{"n":0}]]), patch.object(E,"insert") as insert:
        E.invalidate_account(Mock(),"test")
        assert insert.call_args.args[2][0]["account_id"]==""
    with patch.object(E,"rows",return_value=[]), patch.object(E,"insert") as insert:
        E.invalidate_account(Mock(),"test")
        insert.assert_not_called()


def test_sync_persists_both_sides_and_request_failure():
    broker = Mock()
    broker.trading_accounts.return_value = {"accounts": ["U1"]}
    broker.contract_rules.side_effect = [{"canTradeAcctIds": ["U1"]}, RuntimeError("offline")]
    now = datetime(2026,10,20,tzinfo=UTC)
    with patch.object(E, "ensure_schema"), patch.object(E, "rows", return_value=[{"conid": 123}]), patch.object(E, "insert") as insert:
        totals = E.sync_eligibility(Mock(), "test", broker, clock=lambda: now, sleep=lambda _: None)
        records = [c.args[2][0] for c in insert.call_args_list[1:]]
        assert [(r["side"],r["status"]) for r in records] == [("buy","eligible"),("sell","unknown")]
        assert totals == dict(checked=1,eligible=1,restricted=0,unknown=1)
        assert records[0]["account_id"] == "U1"
        assert len(records[0]["response_sha256"]) == 64


def test_live_consumers_use_gate():
    for sql in (full_tradable_universe(database="q_live"), tradable_symbol_lookup(database="q_live",symbols=["AAPL"])):
        assert "feature_live_tradable_universe_v1" in sql
        assert "feature_live_tradable_universe_v1 FINAL" not in sql


def test_restricted_buys_do_not_prevent_independently_eligible_sells():
    from src.backend.real_live_trading_service import require_tradable_symbol
    with patch("src.backend.real_live_trading_service.tradable_symbol_map",return_value={
        "CLOSE": dict(is_tradable=False,is_sell_eligible=True,ibkr_conid=123,exclusion_reason="restricted_buy")
    }):
        assert require_tradable_symbol("CLOSE",side="SELL")["ibkr_conid"]==123
        with pytest.raises(RuntimeError):
            require_tradable_symbol("CLOSE",side="BUY")


def test_historical_projection_keeps_original_authority():
    from src.backend.query_plans.reference_scanner_asof_v1 import scanner_reference_projection
    sql = scanner_reference_projection(datetime(2026,1,2,tzinfo=UTC))
    assert "feature_tradable_universe_v1" in sql
    assert E.LIVE_VIEW not in sql


def test_real_clickhouse_live_gate():
    """Opt-in real runnable path, isolated explicit SSD tables, always cleaned up."""
    import os
    import uuid
    if not os.environ.get("IBKR_ELIGIBILITY_TEST_CH_URL"):
        pytest.skip("Set IBKR_ELIGIBILITY_TEST_CH_URL for isolated storage integration")
    from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_user, default_clickhouse_password
    client = ClickHouseHttpClient(os.environ["IBKR_ELIGIBILITY_TEST_CH_URL"],default_clickhouse_user(),default_clickhouse_password())
    database = "eligibility_test_" + uuid.uuid4().hex[:12]
    client.execute(f"CREATE DATABASE {database}")
    now = datetime.now(UTC)
    try:
        client.execute(f"""CREATE TABLE {database}.feature_tradable_universe_v1 (
            universe_date Date,ticker String,ibkr_conid Nullable(String),is_tradable UInt8,
            exclusion_reason Nullable(String)
        ) ENGINE=ReplacingMergeTree ORDER BY (universe_date,ticker) SETTINGS storage_policy='live_market_ssd'""")
        E.insert(client,f"{database}.feature_tradable_universe_v1",[
            dict(universe_date=now.date().isoformat(),ticker=t,ibkr_conid=str(c),is_tradable=1,exclusion_reason=None)
            for t,c in [("ALLOW",1),("DENY",2),("UNKNOWN",3),("OTHER",4),("STALE",5),("MISSING",6)]])
        broker = Mock()
        broker.trading_accounts.return_value = {"accounts": ["U1"]}
        def rules(conid, is_buy):
            if conid==3:
                raise RuntimeError("offline")
            return {"canTradeAcctIds": ["U1"] if conid not in (2,4) or not is_buy else ["U2"]}
        broker.contract_rules.side_effect = rules
        result = E.sync_eligibility(client,database,broker,batch_size=5,sleep=lambda _: None,
                                    clock=lambda: now-timedelta(seconds=10))
        assert result["checked"]==5
        def live():
            return {r["ticker"]:r["is_tradable"] for r in E.rows(client,f"SELECT ticker,is_tradable FROM {database}.{E.LIVE_VIEW}")}
        assert live()==dict(ALLOW=1,DENY=0,UNKNOWN=0,OTHER=0,STALE=1,MISSING=0), E.rows(client,f"SELECT conid,side,status FROM {database}.{E.EVIDENCE} ORDER BY conid,side")
        assert E.rows(client,f"SELECT count() n FROM {database}.{E.EVIDENCE}")[0]["n"]==10
        assert E.rows(client,f"SELECT is_tradable,is_sell_eligible FROM {database}.{E.LIVE_VIEW} WHERE ticker='DENY'")[0] == dict(is_tradable=0,is_sell_eligible=1)
        # Newer failed/restricted/expired evidence must win over older approval.
        updates=[]
        for conid,status,expiry in [(1,"unknown",3600),(5,"eligible",-1)]:
            observed=datetime.now(UTC)
            updates.append(dict(observed_date=observed.date().isoformat(),account_id="U1",conid=conid,
                side="buy",observed_at_utc=E.stamp(observed),valid_until_utc=E.stamp(observed+timedelta(seconds=expiry)),
                status=status,reason="test",response_json="{}",response_sha256="",observation_id=uuid.uuid4().hex,
                source_endpoint="/iserver/contract/rules"))
        E.insert(client,f"{database}.{E.EVIDENCE}",updates)
        assert all(v==0 for v in live().values())
        # Selection failure removes prior account approval without rewriting history.
        broker.trading_accounts.side_effect=RuntimeError("authentication lost")
        with pytest.raises(RuntimeError):
            E.sync_eligibility(client,database,broker,sleep=lambda _: None)
        assert all(v==0 for v in live().values())
    finally:
        client.execute(f"DROP DATABASE {database} SYNC")

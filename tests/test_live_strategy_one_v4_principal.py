import pytest

from src.backend import live_strategy_one_v4_principal as live
from scripts.clickhouse import provision_strategy_one_live_v4_runner as provision
from scripts.clickhouse.provision_backtest_v4_runner import desired_plan as backtest_plan
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from tests.test_live_signal_completion_keeper import FakeKazoo


def _lease():
    client = FakeKazoo()
    client.add_listener = lambda listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    return live.LiveV4KeeperLease.acquire(
        session, run_id="strategy-one-live:2026-09-27", owner_id="worker-1")


class Client:
    def __init__(self, user=live.PRINCIPAL):
        self.user = user
        self.closed = False
        self.inserts = []

    def execute(self, sql, **kwargs):
        if sql == "SELECT currentUser()":
            return self.user
        if sql.startswith("SELECT"):
            return "selected"
        self.inserts.append((sql, kwargs))
        return "inserted"

    def close(self):
        self.closed = True


def test_live_v4_plan_is_dedicated_exact_and_excludes_backtest_writes():
    plan = live.desired_plan()
    assert plan.principal != backtest_plan().principal
    assert {"trading_commit_v4", "trading_commit_family_v4",
            "trading_broker_acknowledgement_v4",
            "trading_broker_acknowledgement_v5"} <= plan.insert_arte
    assert {table.name for table in live.live_v4_storage_contracts()} - {
        table.name for table in live.v4_storage_contracts()
    } == {"trading_broker_acknowledgement_v5"}
    assert not any("backtest" in name for name in plan.insert_arte)
    assert plan.insert_arte != backtest_plan().insert_arte
    assert "trading_backtest_cursor_v1" not in plan.insert_arte
    assert "trading_backtest_definition_v1" not in plan.insert_arte
    assert {"strategy_one_configuration_node_v1",
            "strategy_one_configuration_release_v1",
            "live_strategy_one_approval_v1",
            "live_plan_membership_revision_typed_v1",
            "live_plan_assignment_member_typed_v1",
            "live_plan_activated_watch_typed_v1",
            "trading_commit_v1", "trading_commit_v2"} <= plan.select_arte
    assert not {"live_plan_membership_revision_typed_v1",
                "live_plan_assignment_member_typed_v1",
                "live_plan_activated_watch_typed_v1"} & plan.insert_arte
    assert not {"trading_commit_v1", "trading_commit_v2"} & plan.insert_arte
    assert not plan.insert_arte.intersection({
        "bars_v1", "indicators_v1", "liquidity_100ms_v1"})
    assert plan.select_reference == frozenset({("q_live", "market_stock_split_v1")})
    grants = plan.grants()
    assert len(grants) == len(set(grants))
    assert all(" TO strategy_one_live_v4_runner" in grant for grant in grants)
    assert not any(" ON arte.* " in grant or "GRANT CREATE" in grant
                   for grant in grants)
    assert provision.main([]) == 0


def test_preflight_uses_exact_plan_and_checks_principal(monkeypatch):
    calls = []
    monkeypatch.setattr(live, "storage_preflight",
                        lambda client, *, tables: calls.append(("storage", {
                            table.name for table in tables})))
    monkeypatch.setattr(live, "journal_permission_preflight",
                        lambda client, **kwargs: calls.append(("grant", kwargs)))
    live.live_v4_preflight(Client())
    plan = live.desired_plan()
    assert calls[0][1] == plan.insert_arte | live._POLICY_READ
    assert calls[1][1] == {table.name for table in live.PLAN_MEMBERSHIP_TABLES}
    assert calls[2][1]["journal_tables"] == plan.insert_arte
    assert calls[2][1]["read_only_tables"] == (
        plan.select_arte - plan.insert_arte - live.MARKET_READ_TABLES)
    assert calls[2][1]["reference_read_tables"] == plan.select_reference
    with pytest.raises(RuntimeError, match="another principal"):
        live.live_v4_preflight(Client("backtest_v4_runner"))


def test_live_v4_provisioner_grants_only_the_exact_plan(monkeypatch):
    class Admin:
        def __init__(self):
            self.queries = []

        def execute(self, sql):
            self.queries.append(sql)
            if sql == "SELECT currentUser()":
                return "admin"
            if "system.users" in sql:
                return "0"
            return ""

    admin = Admin()
    writer = Client()
    checked = []
    monkeypatch.setattr(provision, "storage_preflight",
                        lambda *_args, **_kwargs: checked.append("storage"))
    monkeypatch.setattr(provision, "verify_tables",
                        lambda *_args: checked.append("configuration"))
    monkeypatch.setattr(provision, "live_v4_preflight",
                        lambda client: checked.append("principal"))
    provision.apply_with_clients(
        admin=admin, credential=lambda **kwargs: "x" * 48,
        client_factory=lambda user, password: writer)
    grants = [sql for sql in admin.queries if sql.startswith("GRANT ")]
    assert set(grants) == set(live.desired_plan().grants())
    assert len(grants) == len(live.desired_plan().grants())
    assert sum(sql.startswith("CREATE USER ") for sql in admin.queries) == 1
    assert not any("INSERT INTO" in sql or "CREATE TABLE" in sql
                   for sql in admin.queries)
    assert checked == ["storage", "configuration", "storage", "storage", "principal"]
    assert writer.closed


def test_client_factory_requires_current_keeper_and_closes_on_failure(monkeypatch):
    calls = []
    client = Client()
    monkeypatch.setattr(live.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(live, "live_v4_preflight",
                        lambda value: calls.append("preflight"))
    factory = lambda endpoint, user, password: calls.append((endpoint, user, password)) or client
    with pytest.raises(RuntimeError, match="Keeper lease"):
        live.open_live_v4_client(
            lease=None, endpoint=live.MANAGED_URL, credential_user=live.PRINCIPAL,
            credential_password="x" * 40, client_factory=factory)
    with pytest.raises(ValueError, match="private principal"):
        live.open_live_v4_client(
            lease=_lease(), endpoint=live.MANAGED_URL, credential_user="backtest_v4_runner",
            credential_password="x" * 40, client_factory=factory)
    assert calls == []
    lease = _lease()
    guarded = live.open_live_v4_client(
        lease=lease, endpoint=live.MANAGED_URL, credential_user=live.PRINCIPAL,
        credential_password="x" * 40, client_factory=factory)
    assert isinstance(guarded, live.LiveV4WriterClient)
    assert guarded.live_v4_lease is lease
    assert guarded.typed_insert_strict is True
    assert isinstance(guarded.typed_insert_dispatch, TypedInsertDispatch)
    assert guarded.typed_insert_dispatch.keeper is lease.owner._session.client
    assert not client.closed
    assert guarded.execute("SELECT currentUser()") == live.PRINCIPAL
    assert guarded.execute("SELECT name FROM system.tables") == "selected"
    with pytest.raises(RuntimeError, match="direct ClickHouse mutation"):
        guarded.execute("INSERT INTO arte.trading_run_v1 VALUES (1)")
    with pytest.raises(RuntimeError, match="direct ClickHouse mutation"):
        guarded.execute("WITH 1 AS x INSERT INTO arte.trading_run_v1 VALUES (x)")
    with pytest.raises(RuntimeError, match="direct ClickHouse mutation"):
        guarded.execute("SELECT 1; SYSTEM FLUSH LOGS")
    assert not client.inserts
    sql = ("INSERT INTO arte.trading_run_v1 (run_id) SETTINGS "
           "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1 "
           "FORMAT JSONEachRow\n{}")
    from src.trading_runtime.arte_typed_insert_dispatch import (
        _Gate, _ZERO_BATCH, _gate_path, _operation_path, _operation_wire,
    )
    run_id = lease.run_id
    query_id = "arte_typed_" + "a" * 64
    with pytest.raises(RuntimeError, match="run gate is absent"):
        guarded.execute_registered_insert(sql, query_id=query_id)
    keeper = lease.owner._session.client
    keeper.create(_gate_path(run_id),
                  _Gate("open", 1, 1, 1, 0, _ZERO_BATCH, "0" * 64,
                        _ZERO_BATCH).wire())
    keeper.create(_operation_path(run_id, query_id),
                  _operation_wire(run_id, "trading_run_v1", query_id,
                                  "token", sql, _ZERO_BATCH, 0, "pending"))
    assert guarded.execute_registered_insert(
        sql, query_id=query_id) == "inserted"
    assert client.inserts == [(sql, {"query_id": query_id})]
    from hashlib import sha256
    from src.trading_runtime.arte_portfolio_sync_dispatch import (
        PortfolioSyncDispatch, _Gate as SyncGate, _Operation,
        _gate_path as sync_gate_path, _query_id as sync_query_id,
    )
    account = "account-1"
    sync_sql = ("INSERT INTO arte.trading_portfolio_sync_snapshot_marker_v1 "
                "(run_id) SETTINGS "
                "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1 "
                "FORMAT JSONEachRow\n{}")
    guarded.typed_sync_insert_dispatch = PortfolioSyncDispatch(keeper)
    sync_id = sync_query_id(run_id, account, 1,
                            "trading_portfolio_sync_snapshot_marker_v1")
    keeper.create(sync_gate_path(run_id), SyncGate(
        account_id=account, revision=1,
        marker=_Operation("pending", sha256(sync_sql.encode()).hexdigest(),
                          "b" * 64)).wire())
    assert guarded.execute_registered_insert(sync_sql, query_id=sync_id) == "inserted"
    assert client.inserts[-1] == (sync_sql, {"query_id": sync_id})
    assert lease.release()
    with pytest.raises(RuntimeError, match="lease lost"):
        guarded.execute_registered_insert(sync_sql, query_id=sync_id)
    client = Client()
    lease = _lease()
    def lose(value):
        lease.release()
    monkeypatch.setattr(live, "live_v4_preflight", lose)
    with pytest.raises(RuntimeError, match="lease lost"):
        live.open_live_v4_client(
            lease=lease, endpoint=live.MANAGED_URL, credential_user=live.PRINCIPAL,
            credential_password="x" * 40,
            client_factory=lambda endpoint, user, password: client)
    assert client.closed
    with pytest.raises(ValueError, match="managed workstation"):
        live.open_live_v4_client(
            lease=_lease(), endpoint="http://other:18123",
            credential_user=live.PRINCIPAL, credential_password="x" * 40,
            client_factory=factory)


def test_live_typed_dispatch_uses_guarded_registered_insert(monkeypatch):
    from src.trading_runtime.arte_typed_insert_dispatch import _ZERO_BATCH

    monkeypatch.setattr(live.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(live, "live_v4_preflight", lambda _client: None)
    lease = _lease()
    raw = Client()
    guarded = live.open_live_v4_client(
        lease=lease, endpoint=live.MANAGED_URL, credential_user=live.PRINCIPAL,
        credential_password="x" * 40,
        client_factory=lambda *_args: raw)
    guarded.typed_insert_dispatch.initialize_new_run(lease.run_id)
    sql = ("INSERT INTO arte.trading_run_v1 (run_id) SETTINGS "
           "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
           "insert_deduplication_token='run-test' FORMAT JSONEachRow\n{}")
    guarded.typed_insert_dispatch.execute_typed_insert(
        guarded, run_id=lease.run_id, table="trading_run_v1",
        token="run-test", sql=sql, batch_id=_ZERO_BATCH,
        batch_last_sequence=0)
    assert len(raw.inserts) == 1
    assert raw.inserts[0][0] == sql
    assert raw.inserts[0][1]["query_id"].startswith("arte_typed_")


def test_live_v4_env_factory_has_no_other_principal_fallback(monkeypatch):
    from src.backend import managed_live_strategy_one_credentials as managed
    monkeypatch.setattr(managed, "load_managed_live_v4_credentials", lambda: False)
    for key in ("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL",
                "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER",
                "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="credential is unavailable"):
        live.live_v4_client_from_env(lease=_lease())
    monkeypatch.setenv("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL", live.MANAGED_URL)
    monkeypatch.setenv("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER", "backtest_v4_runner")
    monkeypatch.setenv("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_PASSWORD", "x" * 48)
    with pytest.raises(RuntimeError, match="credential is unavailable"):
        live.live_v4_client_from_env(lease=_lease())
    monkeypatch.setenv("STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER", live.PRINCIPAL)
    observed = []
    monkeypatch.setattr(live, "open_live_v4_client", lambda **kwargs:
                        observed.append(kwargs) or "client")
    assert live.live_v4_client_from_env(lease=_lease()) == "client"
    assert observed[0]["endpoint"] == live.MANAGED_URL
    assert observed[0]["credential_user"] == live.PRINCIPAL


def test_keeper_live_run_owner_rejects_replacement_and_collision():
    lease = _lease()
    lease.assert_current()
    with pytest.raises(RuntimeError, match="held"):
        live.LiveV4KeeperLease.acquire(
            lease.owner._session, run_id=lease.run_id, owner_id="worker-2")
    assert lease.release()
    with pytest.raises(RuntimeError, match="lease lost"):
        lease.assert_current()
    replacement = live.LiveV4KeeperLease.acquire(
        lease.owner._session, run_id=lease.run_id, owner_id="worker-2")
    assert replacement.epoch > lease.epoch
    assert lease.owner.path(lease.run_id).startswith(
        "/trading/strategy-one-live-v4/v1/")

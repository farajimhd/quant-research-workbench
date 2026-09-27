import pytest

from src.backend import live_strategy_one_v4_principal as live
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

    def execute(self, sql):
        assert sql == "SELECT currentUser()"
        return self.user

    def close(self):
        self.closed = True


def test_live_v4_plan_is_dedicated_exact_and_excludes_backtest_writes():
    plan = live.desired_plan()
    assert plan.principal != backtest_plan().principal
    assert {"trading_commit_v4", "trading_commit_family_v4",
            "trading_broker_acknowledgement_v4"} <= plan.insert_arte
    assert not any("backtest" in name for name in plan.insert_arte)
    assert plan.insert_arte != backtest_plan().insert_arte
    assert "trading_backtest_cursor_v1" not in plan.insert_arte
    assert "trading_backtest_definition_v1" not in plan.insert_arte
    assert {"strategy_one_configuration_node_v1",
            "strategy_one_configuration_release_v1",
            "live_strategy_one_approval_v1"} <= plan.select_arte
    assert not plan.insert_arte.intersection({
        "bars_v1", "indicators_v1", "liquidity_100ms_v1"})
    grants = plan.grants()
    assert len(grants) == len(set(grants))
    assert all(" TO strategy_one_live_v4_runner" in grant for grant in grants)
    assert not any(" ON arte.* " in grant or "GRANT CREATE" in grant
                   for grant in grants)


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
    assert calls[1][1]["journal_tables"] == plan.insert_arte
    assert calls[1][1]["read_only_tables"] == (
        plan.select_arte - plan.insert_arte - live.MARKET_READ_TABLES)
    with pytest.raises(RuntimeError, match="another principal"):
        live.live_v4_preflight(Client("backtest_v4_runner"))


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
    assert live.open_live_v4_client(
        lease=lease, endpoint=live.MANAGED_URL, credential_user=live.PRINCIPAL,
        credential_password="x" * 40, client_factory=factory) is client
    assert client.live_v4_lease is lease
    assert client.typed_insert_strict is True
    assert isinstance(client.typed_insert_dispatch, TypedInsertDispatch)
    assert client.typed_insert_dispatch.keeper is lease.owner._session.client
    assert not client.closed
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

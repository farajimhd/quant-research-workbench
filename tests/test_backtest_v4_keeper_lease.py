"""A V4 Backtest run cannot have two concurrent writers."""
import pytest

from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


RUN_ID = "62908518-9fd4-4a8e-8c90-2162ccb237e1"


def _session():
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    return session


def test_owner_is_exclusive_and_replacement_gets_higher_epoch():
    session = _session()
    first = BacktestV4KeeperLease.acquire(
        session, run_id=RUN_ID, owner_id="worker-1")
    first.assert_current()
    with pytest.raises(RuntimeError, match="held"):
        BacktestV4KeeperLease.acquire(
            session, run_id=RUN_ID, owner_id="worker-2")
    assert first.release()
    with pytest.raises(RuntimeError, match="lease lost"):
        first.assert_current()
    second = BacktestV4KeeperLease.acquire(
        session, run_id=RUN_ID, owner_id="worker-2")
    assert second.epoch > first.epoch
    second.assert_current()
    assert first.owner.path(RUN_ID).startswith(
        "/trading/strategy-one-backtest-v4/v1/")


@pytest.mark.parametrize("run_id", ["", "not-a-uuid", RUN_ID.upper(),
                                     "{" + RUN_ID + "}"])
def test_noncanonical_run_identity_fails_before_claim(run_id):
    with pytest.raises(ValueError, match="run ID"):
        BacktestV4KeeperLease.acquire(
            _session(), run_id=run_id, owner_id="worker")

import pytest

from src.backend import backtest_market_keeper_pool as subject
from src.trading_runtime.keeper_ownership import KeeperUnavailable


class Session:
    def __init__(self, writable=True):
        self.writable = writable
        self.closed = False

    def close(self):
        self.closed = True


def test_read_only_transport_is_reused_without_caching_a_proof(monkeypatch):
    opened = []

    def open_session():
        session = Session()
        opened.append(session)
        return session

    monkeypatch.setattr(subject, "open_workstation_keeper_session", open_session)
    pool = subject.MarketCertificateKeeperPool()
    with pool.borrow() as first, pool.borrow() as second:
        assert first is second
    assert len(opened) == 1
    assert not opened[0].closed
    pool.close()
    pool.close()
    assert opened[0].closed


def test_disconnected_transport_is_closed_and_replaced(monkeypatch):
    opened = []

    def open_session():
        session = Session()
        opened.append(session)
        return session

    monkeypatch.setattr(subject, "open_workstation_keeper_session", open_session)
    pool = subject.MarketCertificateKeeperPool()
    with pool.borrow() as first:
        first.writable = False
        with pool.borrow() as second:
            assert second is not first and second.writable
            assert not first.closed  # An active reader cannot be closed.
        assert not first.closed
    assert first.closed
    pool.close()
    assert second.closed


def test_unwritable_new_transport_fails_closed(monkeypatch):
    session = Session(writable=False)
    monkeypatch.setattr(subject, "open_workstation_keeper_session", lambda: session)
    pool = subject.MarketCertificateKeeperPool()
    with pytest.raises(KeeperUnavailable, match="not writable"):
        with pool.borrow():
            pass
    assert session.closed


def test_pool_shutdown_defers_close_until_last_reader_exits(monkeypatch):
    session = Session()
    monkeypatch.setattr(subject, "open_workstation_keeper_session", lambda: session)
    pool = subject.MarketCertificateKeeperPool()
    with pool.borrow():
        pool.close()
        assert not session.closed
        with pytest.raises(KeeperUnavailable, match="pool is closed"):
            with pool.borrow():
                pass
    assert session.closed

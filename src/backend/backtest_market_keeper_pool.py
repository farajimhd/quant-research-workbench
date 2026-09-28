"""Process-local read-only Keeper transport for market-day certificates.

The pool retains only a Kazoo connection, never a build attestation or lease.
Every Backtest preflight re-reads its exact Keeper proof. A disconnected
transport is replaced under a lock; a failed replacement fails preflight.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator

from src.trading_runtime.keeper_ownership import KeeperUnavailable
from src.trading_runtime.keeper_session import open_workstation_keeper_session


class MarketCertificateKeeperPool:
    def __init__(self) -> None:
        self._lock = Lock()
        self._session: Any | None = None
        self._holders: dict[int, int] = {}
        self._retired: dict[int, Any] = {}
        self._closed = False

    @contextmanager
    def borrow(self) -> Iterator[Any]:
        with self._lock:
            if self._closed:
                raise KeeperUnavailable("Market certificate Keeper pool is closed")
            session = self._session
            if session is not None and not session.writable:
                self._session = None
                identity = id(session)
                if self._holders.get(identity, 0):
                    self._retired[identity] = session
                else:
                    session.close()
                session = None
            if session is None:
                session = open_workstation_keeper_session()
                if not session.writable:
                    session.close()
                    raise KeeperUnavailable("Market certificate Keeper session is not writable")
                self._session = session
            identity = id(session)
            self._holders[identity] = self._holders.get(identity, 0) + 1
        try:
            yield session
        finally:
            retired = None
            with self._lock:
                remaining = self._holders[identity] - 1
                if remaining:
                    self._holders[identity] = remaining
                else:
                    del self._holders[identity]
                    retired = self._retired.pop(identity, None)
            if retired is not None:
                retired.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            session, self._session = self._session, None
            if session is not None:
                self._retired[id(session)] = session
            ready = [self._retired.pop(identity) for identity in tuple(self._retired)
                     if not self._holders.get(identity, 0)]
        for session in ready:
            session.close()


MARKET_CERTIFICATE_KEEPER_POOL = MarketCertificateKeeperPool()
atexit.register(MARKET_CERTIFICATE_KEEPER_POOL.close)

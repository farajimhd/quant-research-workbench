"""Inactive Keeper session-wide activation publication/recovery mutex.

This serializes cooperative writers with cold inventory, but cannot fence a
ClickHouse INSERT already in flight after a Keeper session loss.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.keeper_session import ManagedKeeperSession


class ActivationSessionFence(Protocol):
    def acquire(self, session_key: str, *, owner_id: str) -> int | None: ...
    def is_current(self, session_key: str, *, owner_id: str, epoch: int) -> bool: ...
    def release(self, session_key: str, *, owner_id: str, epoch: int) -> bool: ...


class KeeperActivationSessionFence:
    def __init__(self, session: ManagedKeeperSession) -> None:
        class _Owner(KeeperAssignmentHead):
            @staticmethod
            def path(session_key: str) -> str:
                if (type(session_key) is not str or len(session_key) != 10
                        or session_key[4] != "-" or session_key[7] != "-"):
                    raise ValueError("activation session key is invalid")
                from datetime import date
                try:
                    valid = date.fromisoformat(session_key).isoformat() == session_key
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError("activation session key is invalid")
                return ("/trading/live-activation-session/v1/"
                        + sha256(session_key.encode()).hexdigest())
        self._owner = _Owner(session)

    def acquire(self, session_key: str, *, owner_id: str) -> int | None:
        return self._owner.acquire(session_key, owner_id=owner_id)

    def is_current(self, session_key: str, *, owner_id: str, epoch: int) -> bool:
        return self._owner.is_current(session_key, owner_id=owner_id, epoch=epoch)

    def release(self, session_key: str, *, owner_id: str, epoch: int) -> bool:
        return self._owner.release(session_key, owner_id=owner_id, epoch=epoch)

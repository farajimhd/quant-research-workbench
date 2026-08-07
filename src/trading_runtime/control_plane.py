from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from weakref import WeakKeyDictionary

from src.trading_runtime.strategy_campaign import StrategyCampaignOrchestrator


@dataclass(slots=True)
class TradingControlPlane:
    """Process-wide coordination shared by every run on one broker session.

    Engines retain their focused responsibilities, while account command lanes,
    portfolio admission locks, campaign leases, and safety latches live above
    individual strategy runs.
    """

    account_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    group_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    order_command_lanes: dict[str, asyncio.Lock] = field(default_factory=dict)
    warning_reply_lane: asyncio.Lock = field(default_factory=asyncio.Lock)
    campaigns: StrategyCampaignOrchestrator = field(default_factory=StrategyCampaignOrchestrator)
    risk_states: dict[str, Any] = field(default_factory=dict)
    account_control_modes: dict[str, str] = field(default_factory=dict)

    def account_lock(self, account_id: str) -> asyncio.Lock:
        return self.account_locks.setdefault(account_id, asyncio.Lock())

    def group_lock(self, group_id: str) -> asyncio.Lock:
        return self.group_locks.setdefault(group_id, asyncio.Lock())

    def order_lane(self, account_id: str) -> asyncio.Lock:
        return self.order_command_lanes.setdefault(account_id, asyncio.Lock())


_CONTROL_PLANES: WeakKeyDictionary[object, TradingControlPlane] = WeakKeyDictionary()
_CONTROL_PLANES_LOCK = Lock()


def shared_trading_control_plane(broker: object) -> TradingControlPlane:
    """Return the shared control plane for a concrete broker/session adapter."""

    with _CONTROL_PLANES_LOCK:
        plane = _CONTROL_PLANES.get(broker)
        if plane is None:
            plane = TradingControlPlane()
            _CONTROL_PLANES[broker] = plane
        return plane

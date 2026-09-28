"""Read-only Strategy 1 live release attestation under the dedicated lease.

This proves configuration and operator approval only. Assignment, portfolio,
OMS, broker, activation, and journal recovery must still pass before admission.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable

from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration, certify_strategy_one_configuration,
)
from src.backend.live_strategy_one_approval import (
    KeeperApprovalHeadReader, verify_selected_approval,
)
from src.backend.live_strategy_one_v4_principal import (
    LiveV4KeeperLease, live_v4_client_from_env,
)


@dataclass(frozen=True, slots=True)
class ColdApprovedStrategyOneRelease:
    mode: str
    release: CertifiedStrategyOneConfiguration
    approval_id: str
    approval_hash: str


def cold_approved_strategy_one_release(
    lease: LiveV4KeeperLease, *, mode: str,
    client_factory: Callable[..., Any] = live_v4_client_from_env,
) -> ColdApprovedStrategyOneRelease:
    """Bind the one release to a Keeper-selected approval without SQLite.

    Both release reads are performed under the same live run claim. A changed
    configuration or approval is rejected; this result is not execution
    authority and cannot bypass the remaining cold recovery gates.
    """
    if (not isinstance(lease, LiveV4KeeperLease) or type(mode) is not str
            or mode not in {"paper", "live"}):
        raise ValueError("Strategy 1 live release needs its exact mode and Keeper lease")
    lease.assert_current()
    with closing(client_factory(lease=lease)) as client:
        first = certify_strategy_one_configuration(client)
        approved = verify_selected_approval(
            client, KeeperApprovalHeadReader(lease.owner._session),
            mode=mode, release=first,
        )
        if certify_strategy_one_configuration(client) != first:
            raise RuntimeError("Strategy 1 release changed during live cold read")
        lease.assert_current()
    return ColdApprovedStrategyOneRelease(
        mode, first, approved["approval_id"], approved["content_hash"])

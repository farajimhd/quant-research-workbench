from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Awaitable, Callable

from src.trading_runtime.control_plane import TradingControlPlane
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.portfolio import (
    PortfolioControlMode,
    PortfolioManagementEngine,
    PortfolioSyncState,
)


class AccountRiskState(StrEnum):
    NORMAL = "normal"
    ENTRIES_PAUSED = "entries_paused"
    REDUCE_ONLY = "reduce_only"
    EMERGENCY_EXIT = "emergency_exit"
    RECONCILING = "reconciling"
    FULLY_BLOCKED = "fully_blocked"


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    account_id: str
    account_key: str
    state: AccountRiskState
    reasons: tuple[str, ...]
    metrics: dict[str, float]
    observed_at: datetime
    protection_required: float = 0.0
    protection_coverage: float = 0.0
    internal_reaction_ms: float | None = None


EmergencyCallback = Callable[[RiskEvaluation], Awaitable[None]]


class ContinuousRiskSupervisor:
    """Event-driven account risk state over the portfolio authority.

    It never sizes orders and never calls the broker directly. It narrows the
    account's operational state and asks the OMS to handle any configured
    emergency action.
    """

    def __init__(
        self,
        portfolio: PortfolioManagementEngine,
        *,
        journal: TradingJournal,
        run_id: str,
        emergency_callback: EmergencyCallback | None = None,
        mode: str = "live",
        enabled: bool = True,
        control_plane: TradingControlPlane | None = None,
    ) -> None:
        if mode in {"live", "paper"} and not enabled:
            raise ValueError("Trading Safety Supervisor cannot be disabled for Live or Paper")
        self.portfolio = portfolio
        self.journal = journal
        self.run_id = run_id
        self.emergency_callback = emergency_callback
        self.mode = mode
        self.enabled = enabled
        self.states: dict[str, RiskEvaluation] = (
            control_plane.risk_states
            if control_plane is not None
            else {}
        )
        self._broker_connected = True

    async def evaluate(
        self,
        account_id: str,
        *,
        reason: str,
        protection_required: float = 0.0,
        protection_coverage: float = 0.0,
        internal_reaction_ms: float | None = None,
        allow_operator_resume: bool = False,
        now: datetime | None = None,
    ) -> RiskEvaluation:
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        account = self.portfolio.account_payload(account_id)
        metrics = {key: float(value) for key, value in account.get("metrics", {}).items()}
        if not self.enabled:
            evaluation = RiskEvaluation(
                account_id=account_id,
                account_key=str(account["account_key"]),
                state=AccountRiskState.NORMAL,
                reasons=("safety_supervisor_disabled", reason),
                metrics=metrics,
                observed_at=observed_at,
            )
            self.states[account_id] = evaluation
            self.journal.append(
                run_id=self.run_id,
                category="risk",
                entity_type="continuous_risk_state",
                entity_id=account_id,
                account_id=account_id,
                event_time=observed_at,
                payload={**asdict(evaluation), "enforced": False, "mode": self.mode},
            )
            return evaluation
        portfolio_state = self.portfolio.states[account_id]
        policy = portfolio_state.policy_override or portfolio_state.profile.policy
        sync_state = PortfolioSyncState(str(account["sync_state"]))
        reasons: list[str] = []
        target = AccountRiskState.NORMAL
        if sync_state in {PortfolioSyncState.FULLY_BLOCKED, PortfolioSyncState.DISABLED}:
            target = AccountRiskState.FULLY_BLOCKED
            reasons.append(f"sync_{sync_state.value}")
        elif sync_state not in {PortfolioSyncState.SYNCHRONIZED, PortfolioSyncState.DEGRADED}:
            target = AccountRiskState.RECONCILING
            reasons.append(f"sync_{sync_state.value}")
        if not self._broker_connected:
            target = max(target, AccountRiskState.ENTRIES_PAUSED, key=_risk_severity)
            reasons.append("broker_disconnected")
        if protection_required > protection_coverage + 1e-9:
            target = max(target, AccountRiskState.EMERGENCY_EXIT, key=_risk_severity)
            reasons.append("protection_deficit")
        if (
            internal_reaction_ms is not None
            and internal_reaction_ms > policy.maximum_internal_reaction_ms
        ):
            target = max(target, AccountRiskState.ENTRIES_PAUSED, key=_risk_severity)
            reasons.append("internal_reaction_latency")
        daily_loss = metrics.get("daily_loss", 0.0)
        drawdown = metrics.get("drawdown", 0.0)
        emergency_loss = policy.emergency_loss
        if emergency_loss and daily_loss >= emergency_loss:
            target = max(target, AccountRiskState.EMERGENCY_EXIT, key=_risk_severity)
            reasons.append("emergency_daily_loss")
        elif daily_loss >= policy.maximum_daily_loss or drawdown >= policy.maximum_drawdown:
            target = max(target, AccountRiskState.REDUCE_ONLY, key=_risk_severity)
            if daily_loss >= policy.maximum_daily_loss:
                reasons.append("daily_loss_limit")
            if drawdown >= policy.maximum_drawdown:
                reasons.append("drawdown_limit")
        elif policy.daily_loss_warning and daily_loss >= policy.daily_loss_warning:
            target = max(target, AccountRiskState.ENTRIES_PAUSED, key=_risk_severity)
            reasons.append("daily_loss_warning")
        previous = self.states.get(account_id)
        if (
            target == AccountRiskState.NORMAL
            and not allow_operator_resume
            and (
                (previous is not None and previous.state != AccountRiskState.NORMAL)
                or self.portfolio.states[account_id].control_mode
                != PortfolioControlMode.ENABLED
            )
        ):
            target = AccountRiskState.ENTRIES_PAUSED
            reasons.append("awaiting_operator_resume")
        evaluation = RiskEvaluation(
            account_id=account_id,
            account_key=str(account["account_key"]),
            state=target,
            reasons=tuple(dict.fromkeys([reason, *reasons])),
            metrics=metrics,
            observed_at=observed_at,
            protection_required=protection_required,
            protection_coverage=protection_coverage,
            internal_reaction_ms=internal_reaction_ms,
        )
        self.states[account_id] = evaluation
        self._apply_control(evaluation)
        self.journal.append(
            run_id=self.run_id,
            category="risk",
            entity_type="continuous_risk_state",
            entity_id=account_id,
            account_id=account_id,
            event_time=observed_at,
            payload=asdict(evaluation),
        )
        if target == AccountRiskState.EMERGENCY_EXIT and self.emergency_callback is not None:
            await self.emergency_callback(evaluation)
        return evaluation

    def set_broker_connected(self, connected: bool) -> None:
        self._broker_connected = connected

    async def resume(self, account_id: str, *, reason: str) -> RiskEvaluation:
        self.states.pop(account_id, None)
        evaluation = await self.evaluate(
            account_id,
            reason=f"resume_check:{reason}",
            allow_operator_resume=True,
        )
        if evaluation.state != AccountRiskState.NORMAL:
            raise ValueError(
                "Account cannot resume until broker, portfolio, loss, and protection state are normal"
            )
        self.portfolio.set_control(
            evaluation.account_key,
            PortfolioControlMode.ENABLED,
            reason=reason,
        )
        return evaluation

    def payload(self) -> list[dict[str, object]]:
        return [asdict(self.states[key]) for key in sorted(self.states)]

    def _apply_control(self, evaluation: RiskEvaluation) -> None:
        desired = {
            AccountRiskState.NORMAL: None,
            AccountRiskState.ENTRIES_PAUSED: PortfolioControlMode.ENTRIES_PAUSED,
            AccountRiskState.REDUCE_ONLY: PortfolioControlMode.REDUCE_ONLY,
            AccountRiskState.EMERGENCY_EXIT: PortfolioControlMode.REDUCE_ONLY,
            AccountRiskState.RECONCILING: PortfolioControlMode.ENTRIES_PAUSED,
            AccountRiskState.FULLY_BLOCKED: PortfolioControlMode.DISABLED,
        }[evaluation.state]
        if desired is None:
            return
        current = self.portfolio.by_key[evaluation.account_key].control_mode
        if current != desired:
            self.portfolio.set_control(
                evaluation.account_key,
                desired,
                reason=";".join(evaluation.reasons),
            )


def _risk_severity(state: AccountRiskState) -> int:
    return {
        AccountRiskState.NORMAL: 0,
        AccountRiskState.ENTRIES_PAUSED: 1,
        AccountRiskState.RECONCILING: 2,
        AccountRiskState.REDUCE_ONLY: 3,
        AccountRiskState.EMERGENCY_EXIT: 4,
        AccountRiskState.FULLY_BLOCKED: 5,
    }[state]

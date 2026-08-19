from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.backend.qmd_gateway_client import qmd_ticker_state
from src.backend.canonical_trading_service import canonical_live_snapshot
from src.backend.real_live_trading_service import (
    _approved_configuration_checks,
    configured_real_live_accounts,
    require_tradable_symbol,
    resolve_real_live_accounts,
)
from src.backend.trading_configuration_service import (
    _migrate_draft,
    approved_configuration,
    resolve_session_configuration,
)
from src.backend.trading_runtime_service import trading_journal
from src.backend.trading_action_registry import resolve_trading_action
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.portfolio import PortfolioDecisionStatus, PortfolioManagementEngine
from src.trading_runtime.portfolio_config import configured_portfolio_profiles
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner


SUPPORTED_ACTIONS = {
    "enter_long",
    "add_long",
    "reduce_long",
    "take_profit",
    "exit",
    "enter_short",
    "add_short",
    "reduce_short",
    "cover",
}
MAX_LIVE_SNAPSHOT_AGE_MS = 2_500


async def stage_live_trade_proposal(
    mode: str,
    payload: dict[str, Any],
    *,
    ticker_state: Callable[[str], dict[str, Any]] = qmd_ticker_state,
    tradable_symbol: Callable[[str], dict[str, Any]] = require_tradable_symbol,
    execution_sink: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Validate a confirmed Live/Paper proposal and route it through the shared runtime."""

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"live", "paper"}:
        raise ValueError("Live trade proposals require live or paper mode")
    authority = str(payload.get("authority") or "manual").strip().lower()
    if authority not in {"manual", "semi_automatic"}:
        raise ValueError(
            "Canvas trade proposal authority must be manual or semi_automatic; automatic orders originate from an enabled strategy"
        )
    account_key = str(payload.get("account_id") or "").strip().lower()
    accounts = resolve_real_live_accounts([account_key], account_type=normalized_mode)
    account = accounts[0]
    if account.trading_mode != normalized_mode:
        raise ValueError(
            f"Account {account.account_key} is configured for {account.trading_mode}, not {normalized_mode}"
        )
    configuration_checks = _approved_configuration_checks([account])
    if not configuration_checks or any(row.get("status") != "ready" for row in configuration_checks):
        raise ValueError("The approved Session Profile does not authorize this account and mode")

    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Trade proposal ticker is required")
    action = str(payload.get("action") or "enter_long").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("Trade proposal action is unsupported")
    action_definition = resolve_trading_action(
        action_id=str(payload.get("action_id") or ""),
        runtime_action=action,
    )
    action_id = str(action_definition["action_id"])
    quantity = float(payload.get("quantity") or 0)
    if quantity <= 0:
        raise ValueError("Trade proposal quantity must be positive")

    identity = dict(tradable_symbol(ticker))
    conid = int(identity.get("ibkr_conid") or 0)
    if conid <= 0:
        raise ValueError("Tradable-universe identity omitted a positive IBKR conid")
    requested_conid = int(payload.get("conid") or 0)
    if requested_conid != conid:
        raise ValueError(
            f"Trade proposal conid {requested_conid} does not match current identity {conid} for {ticker}"
        )
    identity_revision = _identity_revision(identity, ticker, conid)
    requested_identity_revision = str(payload.get("identity_revision") or "").strip()
    if requested_identity_revision and requested_identity_revision != identity_revision:
        raise ValueError("Trade proposal identity revision is stale")

    state = dict(ticker_state(ticker))
    if not state.get("found") or state.get("state") != "ready":
        raise ValueError("QMD live ticker state is not ready")
    age_ms = int(state.get("age_ms") or 0)
    if age_ms < 0 or age_ms > MAX_LIVE_SNAPSHOT_AGE_MS:
        raise ValueError("QMD live ticker state is stale")
    row = dict(state.get("row") or {})
    observed_at = _aware_datetime(row.get("last_event_ts"))
    now = datetime.now(UTC)
    if observed_at > now:
        raise ValueError("QMD live ticker state is ahead of the server clock")
    reference_price = float(row.get("last_price") or 0)
    if reference_price <= 0:
        raise ValueError("QMD live ticker state omitted a positive reference price")
    source_sequence = int(state.get("sequence") or 0)
    if source_sequence <= 0:
        raise ValueError("QMD live ticker state omitted its Scanner sequence")

    requested_market = dict(payload.get("market_snapshot") or {})
    if str(requested_market.get("freshness") or "") != "ready":
        raise ValueError("Trade proposal requires a ready chart snapshot")
    requested_observed_at = _aware_datetime(requested_market.get("observed_at"))
    if requested_observed_at > now:
        raise ValueError("Trade proposal chart snapshot is ahead of the server clock")
    if (now - requested_observed_at).total_seconds() * 1_000 > MAX_LIVE_SNAPSHOT_AGE_MS:
        raise ValueError("Trade proposal chart snapshot is stale")
    requested_sequence = str(requested_market.get("source_sequence") or "").strip()
    if not requested_sequence:
        raise ValueError("Trade proposal chart snapshot omitted its price sequence")

    invalidation_price = _optional_positive(payload.get("invalidation_price"))
    profit_target_price = _optional_positive(payload.get("profit_target_price"))
    trailing_amount = _optional_positive(payload.get("trailing_amount"))
    _validate_protection(
        action,
        reference_price,
        invalidation_price=invalidation_price,
        profit_target_price=profit_target_price,
    )

    proposal_id = str(payload.get("proposal_id") or uuid4()).strip()
    if not proposal_id or len(proposal_id) > 128:
        raise ValueError("Trade proposal id is invalid")
    run_id = f"live-control:{normalized_mode}:{account.account_key}"
    duplicate = next(
        (
            record
            for record in trading_journal().recent_records(
                run_id, categories=("trade_proposal",), limit=5_000
            )
            if record.entity_id == proposal_id
        ),
        None,
    )
    if duplicate is not None:
        return dict(duplicate.payload)

    market_snapshot = {
        "authority": str(state.get("authority") or "qmd_gateway_live_memory"),
        "ticker": ticker,
        "observed_at": observed_at.isoformat(),
        "reference_price": reference_price,
        "bid": float(row.get("bid") or 0),
        "ask": float(row.get("ask") or 0),
        "source_sequence": source_sequence,
        "age_ms": age_ms,
        "freshness": "ready",
        "client_chart_observed_at": requested_observed_at.isoformat(),
        "client_chart_sequence": requested_sequence,
    }
    intent = StrategyIntent(
        intent_id=f"proposal:{proposal_id}",
        ticker=ticker,
        event_time=observed_at,
        action=action,
        quantity=quantity,
        reference_price=reference_price,
        invalidation_price=invalidation_price,
        profit_target_price=profit_target_price,
        trailing_amount=trailing_amount,
        urgency=str(payload.get("urgency") or "aggressive_limit"),
        reason=str(payload.get("reason") or "Canvas trade proposal"),
        metadata={
            "origin": "trade_proposal",
            "proposal_id": proposal_id,
            "proposal_authority": authority,
            "action_id": action_id,
            "identity_revision": identity_revision,
            "market_snapshot": market_snapshot,
            "bid": market_snapshot["bid"],
            "ask": market_snapshot["ask"],
            "tick_size": float(payload.get("tick_size") or 0.01),
            "quote_observed_at": observed_at,
            "security_type": "STK",
            "currency": str(payload.get("currency") or "USD").upper(),
            "conid": conid,
            "exchange": str(payload.get("exchange") or "SMART"),
        },
    )
    control = await _validate_control_plane(
        mode=normalized_mode,
        account=account,
        intent=intent,
        conid=conid,
        exchange=str(payload.get("exchange") or "SMART"),
    )
    execution: dict[str, Any] = {
        "broker_submission": False,
        "portfolio_admission_required": True,
        "oms_validation_required": True,
        "reason": "Portfolio or OMS rejected the confirmed proposal.",
    }
    if control["status"] == "validated_pending_broker_runtime":
        sink = execution_sink or _execute_shared_runtime
        runtime_result = await sink(
            mode=normalized_mode,
            run_plan_id=str(control.get("run_plan_id") or ""),
            intent=intent,
            account_id=account.account_id,
            proposal_id=proposal_id,
            proposal_authority=authority,
        )
        execution = {
            "broker_submission": True,
            "portfolio_admission_required": False,
            "oms_validation_required": False,
            "reason": "Confirmed proposal was accepted by the shared runtime.",
            "runtime": runtime_result,
        }
    result = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "authority": authority,
        "mode": normalized_mode,
        "account_key": account.account_key,
        "ticker": ticker,
        "conid": conid,
        "action": action,
        "action_id": action_id,
        "quantity": quantity,
        "identity_revision": identity_revision,
        "market_snapshot": market_snapshot,
        "requested_protection": {
            "invalidation_price": invalidation_price,
            "profit_target_price": profit_target_price,
            "trailing_amount": trailing_amount,
        },
        "status": str(dict(execution.get("runtime") or {}).get("decision", {}).get("status") or control["status"]),
        "portfolio": control["portfolio"],
        "oms": control["oms"],
        "execution": execution,
    }
    trading_journal().append(
        run_id=run_id,
        category="trade_proposal",
        entity_type="trade_proposal_validated",
        entity_id=proposal_id,
        account_id=account.account_key,
        event_time=observed_at,
        payload=result,
    )
    return result


async def _validate_control_plane(
    *,
    mode: str,
    account: Any,
    intent: StrategyIntent,
    conid: int,
    exchange: str,
) -> dict[str, Any]:
    release = approved_configuration(required=True)
    configuration_model = _migrate_draft(dict(release.get("payload") or {}))
    session_options = dict(configuration_model.get("sessions") or {})
    route = next(
        (
            row for row in session_options.get("execution_routes") or []
            if str(row.get("account_key") or "") == account.account_key
            and bool(row.get("enabled", True))
            and bool(row.get("manual_enabled", True))
        ),
        None,
    )
    if session_options:
        resolved_session = resolve_session_configuration(
            configuration_model,
            mode=mode,
            execution_route_id=str(dict(route or {}).get("execution_route_id") or ""),
            resolve_broker_ids=False,
        )
        session_profile_id = str(resolved_session["session_profile"]["session_profile_id"])
    else:
        # Compatibility for isolated control-plane callers; published releases
        # are validated through the Session Profile path above.
        session_profile_id = "interactive-trade-proposal"
    profiles, groups = configured_portfolio_profiles(
        configured_real_live_accounts(),
        configuration=configuration_model,
    )
    mode_profiles = tuple(profile for profile in profiles if profile.mode == mode)
    profile = next(
        (row for row in mode_profiles if row.account_key == account.account_key),
        None,
    )
    if profile is None:
        raise ValueError("Approved Portfolio configuration omitted this account and mode")
    snapshot = canonical_live_snapshot(
        mode,
        ",".join(row.account_key for row in mode_profiles),
    )
    journal = trading_journal()
    run_id = f"live-control:{mode}:{session_profile_id}:{account.account_key}"
    portfolio = PortfolioManagementEngine(
        mode_profiles,
        journal=journal,
        run_id=run_id,
        strategy_id="interactive-trade-proposal",
        strategy_revision=1,
        groups=groups,
        allocation_identity=session_profile_id,
    )
    portfolio.synchronize_canonical(snapshot)
    decision, approved_intent = await portfolio.approve(
        intent,
        account_id=profile.account_id,
    )
    decision_payload = decision.payload()
    if decision.status == PortfolioDecisionStatus.REJECTED or approved_intent is None:
        return {
            "status": "rejected_by_portfolio",
            "portfolio": {**decision_payload, "reservation_status": "not_created"},
            "oms": {"status": "not_evaluated", "reason": "portfolio_rejected"},
        }

    try:
        plan = IbkrStrategyOrderPlanner().plan(
            account_id=profile.account_id,
            instrument=InstrumentContract(
                instrument_id=f"ibkr:{conid}",
                conid=conid,
                symbol=intent.ticker,
                security_type="STK",
                currency=str(intent.metadata.get("currency") or profile.base_currency),
                exchange=exchange,
            ),
            intent=approved_intent,
            strategy_id="interactive-trade-proposal",
            strategy_revision=1,
        )
        if not plan.orders:
            raise ValueError(f"Trade proposal produced no OMS order plan: {intent.action}")
        oms = {
            "status": "validated_not_submitted",
            "order_count": len(plan.orders),
            "batch_count": len(plan.broker_batches),
            "order_types": sorted({row.orderType for row in plan.orders}),
            "sides": sorted({row.side for row in plan.orders}),
            "protection_order_count": sum(
                1 for row in plan.orders if row.parentId or row.orderType in {"STP", "STP LMT", "TRAIL"}
            ),
            "broker_risk_validation": "pending_authorized_runtime",
        }
        status = "validated_pending_broker_runtime"
    except (TypeError, ValueError) as exc:
        oms = {"status": "rejected", "reason": str(exc)}
        status = "rejected_by_oms"
    finally:
        portfolio.release_intent(
            approved_intent.intent_id,
            reason="proposal_validation_completed_without_broker_submission",
        )
    return {
        "status": status,
        "portfolio": {**decision_payload, "reservation_status": "released"},
        "oms": oms,
        "run_plan_id": f"session:{session_profile_id}",
    }


async def _execute_shared_runtime(**kwargs: Any) -> dict[str, Any]:
    from src.backend.live_strategy_runtime_service import LIVE_STRATEGY_RUNTIME

    return await LIVE_STRATEGY_RUNTIME.submit_external_intent(**kwargs)


def _identity_revision(identity: dict[str, Any], ticker: str, conid: int) -> str:
    universe_date = str(identity.get("universe_date") or "").strip()
    if not universe_date:
        raise ValueError("Tradable-universe identity omitted its revision date")
    return f"tradable-universe:{universe_date}:{ticker}:{conid}"


def _aware_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Trade proposal snapshot timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Trade proposal snapshot timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _optional_positive(value: Any) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    if number <= 0:
        raise ValueError("Requested protection prices must be positive")
    return number


def _validate_protection(
    action: str,
    reference_price: float,
    *,
    invalidation_price: float | None,
    profit_target_price: float | None,
) -> None:
    long_entry = action in {"enter_long", "add_long"}
    short_entry = action in {"enter_short", "add_short"}
    if long_entry:
        if invalidation_price is not None and invalidation_price >= reference_price:
            raise ValueError("Long proposal stop must be below the current reference price")
        if profit_target_price is not None and profit_target_price <= reference_price:
            raise ValueError("Long proposal target must be above the current reference price")
    if short_entry:
        if invalidation_price is not None and invalidation_price <= reference_price:
            raise ValueError("Short proposal stop must be above the current reference price")
        if profit_target_price is not None and profit_target_price >= reference_price:
            raise ValueError("Short proposal target must be below the current reference price")

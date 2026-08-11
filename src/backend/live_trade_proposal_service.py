from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from src.backend.qmd_gateway_client import qmd_ticker_state
from src.backend.real_live_trading_service import (
    _approved_configuration_checks,
    require_tradable_symbol,
    resolve_real_live_accounts,
)
from src.backend.trading_runtime_service import trading_journal


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


def stage_live_trade_proposal(
    mode: str,
    payload: dict[str, Any],
    *,
    ticker_state: Callable[[str], dict[str, Any]] = qmd_ticker_state,
    tradable_symbol: Callable[[str], dict[str, Any]] = require_tradable_symbol,
) -> dict[str, Any]:
    """Validate and journal a Live/Paper semantic proposal without executing it.

    Broker submission remains disabled until the shared Live/Paper runtime is
    explicitly deployed. That runtime must consume this evidence and repeat
    Portfolio admission and OMS validation immediately before any command.
    """

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"live", "paper"}:
        raise ValueError("Live trade proposals require live or paper mode")
    authority = str(payload.get("authority") or "manual").strip().lower()
    if authority not in {"manual", "semi_automatic"}:
        raise ValueError("Trade proposal authority must be manual or semi_automatic")
    account_key = str(payload.get("account_id") or "").strip().lower()
    accounts = resolve_real_live_accounts([account_key], account_type=normalized_mode)
    account = accounts[0]
    if account.trading_mode != normalized_mode:
        raise ValueError(
            f"Account {account.account_key} is configured for {account.trading_mode}, not {normalized_mode}"
        )
    configuration_checks = _approved_configuration_checks([account])
    if not configuration_checks or any(row.get("status") != "ready" for row in configuration_checks):
        raise ValueError("The approved Run Plan does not authorize this account and mode")

    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Trade proposal ticker is required")
    action = str(payload.get("action") or "enter_long").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("Trade proposal action is unsupported")
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
    result = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "authority": authority,
        "mode": normalized_mode,
        "account_key": account.account_key,
        "ticker": ticker,
        "conid": conid,
        "action": action,
        "quantity": quantity,
        "identity_revision": identity_revision,
        "market_snapshot": market_snapshot,
        "requested_protection": {
            "invalidation_price": invalidation_price,
            "profit_target_price": profit_target_price,
            "trailing_amount": trailing_amount,
        },
        "status": "validated_pending_runtime",
        "execution": {
            "broker_submission": False,
            "portfolio_admission_required": True,
            "oms_validation_required": True,
            "reason": "Shared Live/Paper runtime deployment requires separate broker authorization.",
        },
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

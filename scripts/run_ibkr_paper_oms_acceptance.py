from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import (
    ExecutionEnvelope,
    ExecutionMarketDataProvider,
    ExecutionMarketSnapshot,
    ExecutionPolicy,
    ExecutionPolicyName,
    ProtectionProfile,
    ProtectionSlice,
    StopRule,
    StopRuleType,
)
from src.trading_runtime.ibkr_client import IbkrClientPortalAdapter
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import BrokerCommunicationPolicy, OrderManagementEngine
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\trading\ibkr-paper-acceptance")
EXECUTION_CONFIRMATION = "I_UNDERSTAND_THIS_PLACES_PAPER_ORDERS"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authenticated IBKR Paper OMS acceptance preflight and entry/cancel scenario."
    )
    parser.add_argument("--base-url", default="https://localhost:5000/v1/api")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--conid", type=int, required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--bid", type=float, required=True)
    parser.add_argument("--ask", type=float, required=True)
    parser.add_argument("--tick-size", type=float, required=True)
    parser.add_argument("--quantity", type=float, default=1)
    parser.add_argument("--maximum-buy-price", type=float, required=True)
    parser.add_argument("--stop-price", type=float, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--verify-tls", action="store_true")
    parser.add_argument("--output-root", type=Path, default=RUNTIME_ROOT)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, object]:
    if args.ask < args.bid or min(args.bid, args.ask, args.tick_size, args.quantity) <= 0:
        raise ValueError("Bid/ask, tick size, and quantity must be positive and non-crossed")
    if args.maximum_buy_price < args.ask:
        raise ValueError("maximum-buy-price must permit the supplied ask")
    if not 0 < args.stop_price < args.bid:
        raise ValueError("long paper acceptance stop must be positive and below the supplied bid")
    if args.execute and args.confirmation != EXECUTION_CONFIRMATION:
        raise ValueError(
            f"--execute requires --confirmation {EXECUTION_CONFIRMATION}"
        )
    if args.execute and not args.account_id.upper().startswith("DU"):
        raise ValueError("--execute is restricted to IBKR paper account ids beginning with DU")

    run_id = f"paper-acceptance-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    journal = TradingJournal(run_dir / "trading.sqlite3")
    broker = IbkrClientPortalAdapter(
        args.base_url,
        verify_tls=args.verify_tls,
        mode=TradingMode.PAPER,
    )
    manager: OrderManagementEngine | None = None
    try:
        await broker.initialize()
        accounts = await broker.accounts()
        if args.account_id not in accounts:
            raise RuntimeError(
                f"Configured paper account {args.account_id} was not returned by IBKR: {accounts}"
            )
        summary, ledger, positions, live_orders = await asyncio.gather(
            broker.account_summary(args.account_id),
            broker.account_ledger(args.account_id),
            broker.positions(args.account_id),
            broker.live_orders(),
        )
        policy = ExecutionPolicy(
            policy_id="paper-acceptance-adaptive",
            revision=1,
            name=ExecutionPolicyName.ADAPTIVE_REGULAR,
            envelope=ExecutionEnvelope(
                maximum_buy_price=args.maximum_buy_price,
                deadline_ms=1_000,
                maximum_reprices=4,
                minimum_reprice_interval_ms=50,
            ),
        )
        protection = ProtectionProfile(
            profile_id="paper-acceptance-stop",
            revision=1,
            slices=(
                ProtectionSlice(
                    "all",
                    1.0,
                    StopRule(StopRuleType.FIXED_PRICE, price=args.stop_price),
                ),
            ),
        )
        intent = StrategyIntent(
            intent_id=f"{run_id}-entry",
            ticker=args.ticker.upper(),
            event_time=datetime.now(timezone.utc),
            action="enter_long",
            quantity=args.quantity,
            reference_price=args.ask,
            execution_policy=policy,
            protection_profile=protection,
            metadata={"assignment_id": run_id},
        )
        instrument = InstrumentContract(
            args.ticker.upper(),
            args.conid,
            args.ticker.upper(),
            "STK",
            "USD",
        )
        planner = IbkrStrategyOrderPlanner()

        def plan(strategy_intent: StrategyIntent, account_id: str, _event):
            return planner.plan(
                account_id=account_id,
                instrument=instrument,
                intent=strategy_intent,
                strategy_id="paper-acceptance",
                strategy_revision=1,
                # Preview the same touch-priced root that OMS will submit
                # after applying its fresh execution quote. The planner's
                # generic five-basis-point fallback can violate an
                # instrument's minimum tick before OMS has a chance to
                # normalize it.
                limit_offset_bps=0,
            )

        planned = plan(intent, args.account_id, None)
        preview = await broker.preview_orders(args.account_id, list(planned.orders))
        result: dict[str, object] = {
            "run_id": run_id,
            "mode": "paper",
            "executed": bool(args.execute),
            "account_id": args.account_id,
            "accounts_discovered": accounts,
            "account_summary_timestamp": summary.timestamp.isoformat(),
            "ledger_timestamp": ledger.timestamp.isoformat(),
            "position_count": len(positions),
            "open_order_count": len(
                [order for order in live_orders if order.account == args.account_id]
            ),
            "intent": intent.payload(),
            "preview": preview,
        }
        if args.execute:
            risk = RiskAuthority()
            await risk.prime(broker, [args.account_id])
            market_data = ExecutionMarketDataProvider()
            market_data.update(
                ExecutionMarketSnapshot(
                    args.ticker.upper(),
                    args.bid,
                    args.ask,
                    args.tick_size,
                    datetime.now(timezone.utc),
                    "operator-supplied-qmd-snapshot",
                )
            )
            manager = OrderManagementEngine(
                broker=broker,
                planner=plan,
                risk=risk,
                journal=journal,
                run_id=run_id,
                strategy_id="paper-acceptance",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy.from_environment(),
                execution_market_data=market_data,
                enforce_wall_clock_quote_freshness=True,
            )
            submitted = await manager.submit_intent(intent, account_id=args.account_id, event=None)
            result["submitted"] = asdict(submitted)
            result["cancel_responses"] = await manager.kill_entries(
                args.account_id,
                reason="paper_acceptance_cleanup",
            )
            await asyncio.sleep(0.25)
            result["reconciled"] = [asdict(item) for item in await manager.reconcile()]
        (run_dir / "acceptance.json").write_text(
            json.dumps(result, indent=2, default=str),
            encoding="utf-8",
        )
        return {"run_dir": str(run_dir), **result}
    finally:
        if manager is not None:
            await manager.close()
        journal.close()


def main() -> None:
    result = asyncio.run(run(arguments()))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import queue
import sys
import threading
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.qmd_gateway_client import qmd_current_structure_snapshot
from src.backend.trading_runtime_service import trading_journal
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.ibkr_client import IbkrClientPortalAdapter
from src.trading_runtime.portfolio_config import configured_portfolio_profiles_for_runtime
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.strategy_activation import (
    strategy_observation_from_market_row,
    strategy_observation_from_signal_occurrence,
)
from src.trading_runtime.strategy_campaign import campaign_state
from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    StrategyAssignment,
    StrategyPermissions,
)
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.strategy_registry import strategy_executor


NEW_YORK = ZoneInfo("America/New_York")
ACTIVATION_CHECKPOINT_RUN_ID = "live-strategy-runtime:activations"


class LiveStrategyRuntimeSupervisor:
    """Consume accepted Signal Stream deliveries through the shared runtime."""

    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10_000)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._activations: dict[str, dict[str, Any]] = {}
        self._status: dict[str, Any] = {
            "running": False,
            "state": "stopped",
            "mode": self.mode,
            "queued": 0,
            "processed": 0,
            "failed": 0,
            "last_error": "",
            "active_runs": [],
        }

    @property
    def mode(self) -> str:
        value = os.environ.get("TRADING_STRATEGY_RUNTIME_MODE", "paper").strip().lower()
        return value if value in {"paper", "live"} else "disabled"

    def start(self) -> None:
        running_under_test = bool(os.environ.get("PYTEST_CURRENT_TEST")) or any(
            name == "tests" or name.startswith("tests.") for name in sys.modules
        )
        if running_under_test or self.mode == "disabled":
            with self._lock:
                self._status.update({"running": False, "state": "disabled", "mode": self.mode})
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._hydrate_activations()
            self._thread = threading.Thread(
                target=self._run_thread,
                name="live-strategy-runtime",
                daemon=True,
            )
            self._status.update({"running": True, "state": "starting", "mode": self.mode})
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=10)
        with self._lock:
            self._status.update({"running": False, "state": "stopped"})

    def submit(self, deliveries: list[dict[str, Any]]) -> int:
        accepted = 0
        for delivery in deliveries:
            try:
                delivery_copy = deepcopy(delivery)
                self._queue.put_nowait({"kind": "signal", "delivery": delivery_copy})
            except queue.Full:
                with self._lock:
                    self._status.update({
                        "state": "degraded",
                        "last_error": "Strategy activation queue capacity is exhausted",
                    })
                break
            delivery_id = str(delivery_copy.get("delivery_id") or "")
            if delivery_id:
                with self._lock:
                    self._activations[delivery_id] = delivery_copy
            accepted += 1
        if accepted:
            self._save_activations()
        with self._lock:
            self._status["queued"] = self._queue.qsize()
        return accepted

    def submit_market_rows(self, rows: list[dict[str, Any]], *, as_of: Any) -> int:
        with self._lock:
            activations = list(self._activations.values())
        by_ticker: dict[str, list[dict[str, Any]]] = {}
        for delivery in activations:
            by_ticker.setdefault(str(delivery.get("ticker") or "").upper(), []).append(delivery)
        accepted = 0
        for row in rows:
            ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
            for delivery in by_ticker.get(ticker, []):
                try:
                    self._queue.put_nowait({
                        "kind": "market_row",
                        "delivery": deepcopy(delivery),
                        "row": deepcopy(row),
                        "as_of": str(as_of or datetime.now(tz=ZoneInfo("UTC")).isoformat()),
                    })
                except queue.Full:
                    return accepted
                accepted += 1
        with self._lock:
            self._status["queued"] = self._queue.qsize()
            self._status["active_ticker_count"] = len(by_ticker)
        return accepted

    async def submit_external_intent(
        self,
        *,
        mode: str,
        run_plan_id: str,
        intent: Any,
        account_id: str,
        proposal_id: str,
        proposal_authority: str,
    ) -> dict[str, Any]:
        """Execute one confirmed Canvas proposal on the supervisor event loop."""

        if mode != self.mode:
            raise ValueError(
                f"The shared strategy runtime is configured for {self.mode}, not {mode}"
            )
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("The shared strategy runtime is not running")
        result: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        try:
            self._queue.put_nowait({
                "kind": "external_intent",
                "mode": mode,
                "run_plan_id": run_plan_id,
                "intent": intent,
                "account_id": account_id,
                "proposal_id": proposal_id,
                "proposal_authority": proposal_authority,
                "result": result,
            })
        except queue.Full as exc:
            raise RuntimeError("Strategy runtime queue capacity is exhausted") from exc
        with self._lock:
            self._status["queued"] = self._queue.qsize()
        try:
            return await asyncio.wait_for(asyncio.wrap_future(result), timeout=20)
        except TimeoutError:
            result.cancel()
            raise RuntimeError("Timed out waiting for the shared strategy runtime") from None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._status)

    def _update_active_runs(self, runtimes: dict[str, dict[str, Any]]) -> None:
        active_runs = []
        for principal_id, state in sorted(runtimes.items()):
            runtime = state["runtime"]
            active_runs.append({
                "run_id": runtime.config.run_id,
                "principal_id": principal_id,
                "execution_mode": "manual" if state.get("strategy") is None else "strategy",
                "strategy_id": runtime.config.strategy_id,
                "strategy_revision": runtime.config.strategy_revision,
                "account_ids": list(runtime.config.account_ids),
                "configuration_revision_id": str(state.get("revision_id") or ""),
            })
        with self._lock:
            self._status["active_runs"] = active_runs

    def _run_thread(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        broker: IbkrClientPortalAdapter | None = None
        runtimes: dict[str, dict[str, Any]] = {}
        try:
            while not self._stop.is_set():
                delivery = await asyncio.to_thread(self._queue.get)
                if delivery is None:
                    break
                try:
                    kind = str(delivery.get("kind") or "signal")
                    if kind == "market_row":
                        broker = await self._process_market_row(delivery, broker, runtimes)
                    elif kind == "external_intent":
                        broker, external_result = await self._process_external_intent(
                            delivery, broker, runtimes
                        )
                        delivery["result"].set_result(external_result)
                    else:
                        broker = await self._process(dict(delivery.get("delivery") or delivery), broker, runtimes)
                except Exception as exc:
                    result = delivery.get("result")
                    if isinstance(result, concurrent.futures.Future) and not result.done():
                        result.set_exception(exc)
                    with self._lock:
                        self._status.update({
                            "state": "degraded",
                            "failed": int(self._status.get("failed") or 0) + 1,
                            "last_error": str(exc),
                            "last_failure_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
                        })
                else:
                    with self._lock:
                        self._status.update({
                            "state": "ready",
                            "processed": int(self._status.get("processed") or 0) + 1,
                            "last_error": "",
                            "last_processed_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
                        })
                finally:
                    with self._lock:
                        self._status["queued"] = self._queue.qsize()
        finally:
            for state in runtimes.values():
                try:
                    await state["runtime"].finish(status="stopped")
                except Exception:
                    pass
            self._update_active_runs({})

    async def _process(
        self,
        delivery: dict[str, Any],
        broker: IbkrClientPortalAdapter | None,
        runtimes: dict[str, dict[str, Any]],
    ) -> IbkrClientPortalAdapter:
        from src.backend.real_live_trading_service import ibkr_base_url
        from src.backend.trading_configuration_service import approved_runtime_configuration_snapshot

        mode = self.mode
        if mode not in {"paper", "live"}:
            raise ValueError("Strategy runtime execution mode is disabled")
        snapshot = approved_runtime_configuration_snapshot(
            mode,
            run_plan_id=str(delivery.get("run_plan_id") or ""),
        )
        runtime_configuration = dict(snapshot["payload"])
        run_plan_id = str(runtime_configuration["run_plan"]["run_plan_id"])
        state = runtimes.get(run_plan_id)
        revision_id = str(snapshot.get("revision_id") or "")
        if state is not None and str(state.get("revision_id") or "") != revision_id:
            await state["runtime"].finish(status="configuration_replaced")
            runtimes.pop(run_plan_id, None)
            state = None
        if broker is None:
            broker = IbkrClientPortalAdapter(
                ibkr_base_url(),
                verify_tls=False,
                mode=TradingMode(mode),
            )
        if state is None:
            state = await _build_runtime(snapshot, broker)
            runtimes[run_plan_id] = state
            self._update_active_runs(runtimes)
        else:
            _upsert_runtime_assignments(state, runtime_configuration)

        occurrence = dict(delivery.get("occurrence") or {})
        observation = strategy_observation_from_signal_occurrence(occurrence)
        assignments = [
            row for row in state["strategy"].assignments()
            if row.ticker == observation.ticker
        ]
        if not assignments:
            raise ValueError(
                f"Signal activation has no point-in-time assignment for {observation.ticker}"
            )
        for assignment in assignments:
            positions = await _cached_positions(state, assignment.account_id)
            position = next((row for row in positions if int(row.conid) == assignment.conid), None)
            await state["runtime"].process_account_strategy_observation(
                _observation_with_position(observation, position),
                assignment.account_id,
            )
        return broker

    async def _process_external_intent(
        self,
        item: dict[str, Any],
        broker: IbkrClientPortalAdapter | None,
        runtimes: dict[str, dict[str, Any]],
    ) -> tuple[IbkrClientPortalAdapter, dict[str, Any]]:
        delivery = {"run_plan_id": str(item.get("run_plan_id") or "")}
        broker, state = await self._runtime_state(delivery, broker, runtimes)
        intent = item["intent"]
        conid = int(intent.metadata.get("conid") or 0)
        if conid <= 0:
            raise ValueError("Confirmed proposal omitted its point-in-time instrument identity")
        state["planner"].upsert_instrument(
            InstrumentContract(
                instrument_id=f"ibkr:{conid}",
                conid=conid,
                symbol=intent.ticker,
                security_type="STK",
                currency=str(intent.metadata.get("currency") or "USD"),
                exchange=str(intent.metadata.get("exchange") or "SMART"),
            )
        )
        result = await state["runtime"].submit_external_intent(
            intent,
            account_id=str(item["account_id"]),
            proposal_id=str(item["proposal_id"]),
            proposal_authority=str(item["proposal_authority"]),
        )
        return broker, result

    async def _process_market_row(
        self,
        item: dict[str, Any],
        broker: IbkrClientPortalAdapter | None,
        runtimes: dict[str, dict[str, Any]],
    ) -> IbkrClientPortalAdapter:
        delivery = dict(item.get("delivery") or {})
        broker, state = await self._runtime_state(delivery, broker, runtimes)
        ticker = str(delivery.get("ticker") or "").upper()
        assignments = [row for row in state["strategy"].assignments() if row.ticker == ticker]
        market_row = dict(item.get("row") or {})
        if assignments and any(
            bool(dict(assignment.parameters.get("structural_entry") or {}).get("enabled"))
            for assignment in assignments
        ):
            structure = await asyncio.to_thread(
                qmd_current_structure_snapshot,
                ticker,
                timeframe="1s",
            )
            observation_time = _aware_datetime(item.get("as_of"))
            structure_time = _aware_datetime(
                structure.get("bar_end") or structure.get("observed_at")
            )
            if observation_time is None or structure_time is None:
                raise RuntimeError("Live structural strategy input omitted a causal timestamp")
            if structure_time > observation_time:
                raise RuntimeError(
                    "Live structural strategy snapshot is newer than its market observation"
                )
            market_row.update({
                key: deepcopy(value)
                for key, value in structure.items()
                if str(key).startswith("qmd_structure_")
            })
        for assignment in assignments:
            positions = await _cached_positions(state, assignment.account_id)
            position = next((row for row in positions if int(row.conid) == assignment.conid), None)
            observation = strategy_observation_from_market_row(
                market_row,
                observed_at=item.get("as_of"),
                position_quantity=float(position.position if position else 0),
                average_price=float(position.avgPrice if position else 0),
            )
            await state["runtime"].process_account_strategy_observation(
                observation, assignment.account_id
            )
        return broker

    async def _runtime_state(
        self,
        delivery: dict[str, Any],
        broker: IbkrClientPortalAdapter | None,
        runtimes: dict[str, dict[str, Any]],
    ) -> tuple[IbkrClientPortalAdapter, dict[str, Any]]:
        from src.backend.real_live_trading_service import ibkr_base_url
        from src.backend.trading_configuration_service import (
            approved_runtime_configuration_snapshot,
            approved_session_configuration_snapshot,
        )

        mode = self.mode
        requested_principal = str(delivery.get("run_plan_id") or "")
        manual = requested_principal.startswith("session:")
        snapshot = (
            approved_session_configuration_snapshot(
                mode,
                session_profile_id=requested_principal.removeprefix("session:"),
            )
            if manual
            else approved_runtime_configuration_snapshot(mode, run_plan_id=requested_principal)
        )
        configuration = dict(snapshot["payload"])
        run_plan_id = (
            f"session:{configuration['session_profile']['session_profile_id']}"
            if manual
            else str(configuration["run_plan"]["run_plan_id"])
        )
        state = runtimes.get(run_plan_id)
        revision_id = str(snapshot.get("revision_id") or "")
        if state is not None and str(state.get("revision_id") or "") != revision_id:
            await state["runtime"].finish(status="configuration_replaced")
            runtimes.pop(run_plan_id, None)
            state = None
        if broker is None:
            broker = IbkrClientPortalAdapter(ibkr_base_url(), verify_tls=False, mode=TradingMode(mode))
        if state is None:
            state = await (_build_manual_runtime(snapshot, broker) if manual else _build_runtime(snapshot, broker))
            runtimes[run_plan_id] = state
            self._update_active_runs(runtimes)
        elif not manual:
            _upsert_runtime_assignments(state, configuration)
        return broker, state

    def _hydrate_activations(self) -> None:
        checkpoint = trading_journal().load_checkpoint(ACTIVATION_CHECKPOINT_RUN_ID)
        state = dict(dict(checkpoint or {}).get("state") or {})
        today = datetime.now(NEW_YORK).date().isoformat()
        rows = state.get("deliveries") if str(state.get("session_key") or "") == today else []
        self._activations = {
            str(row.get("delivery_id") or ""): dict(row)
            for row in rows or []
            if str(row.get("delivery_id") or "")
        }

    def _save_activations(self) -> None:
        now = datetime.now(tz=ZoneInfo("UTC"))
        with self._lock:
            rows = list(self._activations.values())
        trading_journal().save_checkpoint(
            ACTIVATION_CHECKPOINT_RUN_ID,
            now.isoformat(),
            {
                "session_key": now.astimezone(NEW_YORK).date().isoformat(),
                "deliveries": rows,
            },
            now,
        )


def _observation_with_position(observation, position):
    return replace(
        observation,
        position_quantity=float(position.position if position else 0),
        average_price=float(position.avgPrice if position else 0),
    )


async def _build_runtime(
    snapshot: dict[str, Any], broker: IbkrClientPortalAdapter
) -> dict[str, Any]:
    configuration = dict(snapshot["payload"])
    strategy_config = dict(configuration["strategy"])
    registration = strategy_executor(
        str(strategy_config["strategy_id"]), int(strategy_config["revision"])
    )
    bindings = {
        str(row.get("account_key") or ""): row
        for row in dict(configuration.get("accounts") or {}).get("bindings") or []
    }
    assignments = [
        _assignment(row, bindings, configuration)
        for row in configuration.get("assignments") or []
    ]
    strategy = registration.strategy_factory(assignments)
    instruments = {
        row.ticker: InstrumentContract(
            instrument_id=f"ibkr:{row.conid}",
            conid=row.conid,
            symbol=row.ticker,
            security_type="STK",
            currency="USD",
            exchange="SMART",
        )
        for row in assignments
    }
    run_plan_id = str(configuration["run_plan"]["run_plan_id"])
    planner = RuntimeIbkrStrategyOrderPlanner(
        instruments,
        strategy_id=registration.strategy_id,
        strategy_revision=registration.revision,
        run_id=f"{snapshot['mode']}:{run_plan_id}",
        limit_offset_bps=float(dict(configuration.get("oms") or {}).get("limit_offset_bps") or 5),
    )
    from src.backend.real_live_trading_service import configured_real_live_accounts

    enabled_binding_keys = {
        key
        for key, row in bindings.items()
        if bool(row.get("enabled", True))
        and str(snapshot["mode"]) in set(row.get("modes") or [])
    }
    interactive_account_ids = [
        row.account_id
        for row in configured_real_live_accounts()
        if row.account_key in enabled_binding_keys
        and row.trading_mode == str(snapshot["mode"])
        and row.account_id
    ]
    account_ids = tuple(
        dict.fromkeys([*(row.account_id for row in assignments), *interactive_account_ids])
    )
    if not account_ids:
        raise ValueError("Enabled Run Plan has no resolved broker account for strategy or interactive trading")
    runtime = TradingRuntime(
        RunConfig(
            mode=RunMode(str(snapshot["mode"])),
            strategy_id=registration.strategy_id,
            strategy_revision=registration.revision,
            account_ids=account_ids,
            anchor_date=datetime.now(NEW_YORK).date(),
            run_id=f"{snapshot['mode']}:{run_plan_id}:{datetime.now(NEW_YORK).date().isoformat()}",
            run_plan_id=run_plan_id,
            safety_supervisor_enabled=True,
        ),
        broker,
        strategy,
        trading_journal(),
        intent_planner=planner,
        portfolio_configuration=dict(snapshot["configuration_model"]),
    )
    await runtime.initialize()
    return {
        "revision_id": str(snapshot.get("revision_id") or ""),
        "runtime": runtime,
        "strategy": strategy,
        "planner": planner,
        "bindings": bindings,
        "positions_cache": {},
    }


async def _build_manual_runtime(
    snapshot: dict[str, Any], broker: IbkrClientPortalAdapter
) -> dict[str, Any]:
    configuration = dict(snapshot["payload"])
    session_profile = dict(configuration["session_profile"])
    session_profile_id = str(session_profile["session_profile_id"])
    bindings = {
        str(row.get("account_key") or ""): row
        for row in dict(configuration.get("accounts") or {}).get("bindings") or []
    }
    account_ids = tuple(
        dict.fromkeys(
            str(row.get("source_account_id") or "").strip()
            for row in bindings.values()
            if bool(row.get("enabled", True)) and str(row.get("source_account_id") or "").strip()
        )
    )
    if not account_ids:
        raise ValueError("Enabled Session Profile has no resolved broker account")
    run_id = f"{snapshot['mode']}:session:{session_profile_id}:{datetime.now(NEW_YORK).date().isoformat()}"
    planner = RuntimeIbkrStrategyOrderPlanner(
        {},
        strategy_id="manual",
        strategy_revision=0,
        run_id=run_id,
        limit_offset_bps=float(dict(configuration.get("oms") or {}).get("limit_offset_bps") or 5),
    )
    runtime = TradingRuntime(
        RunConfig(
            mode=RunMode(str(snapshot["mode"])),
            strategy_id="",
            strategy_revision=0,
            account_ids=account_ids,
            anchor_date=datetime.now(NEW_YORK).date(),
            run_id=run_id,
            run_plan_id=session_profile_id,
            safety_supervisor_enabled=True,
        ),
        broker,
        None,
        trading_journal(),
        intent_planner=planner,
        portfolio_configuration=dict(snapshot["configuration_model"]),
    )
    await runtime.initialize()
    return {
        "revision_id": str(snapshot.get("revision_id") or ""),
        "runtime": runtime,
        "strategy": None,
        "planner": planner,
        "bindings": bindings,
        "positions_cache": {},
    }


async def _cached_positions(state: dict[str, Any], account_id: str) -> list[Any]:
    now = asyncio.get_running_loop().time()
    cache = dict(state.setdefault("positions_cache", {}).get(account_id) or {})
    if now - float(cache.get("loaded_at") or 0) <= 1.0:
        return list(cache.get("rows") or [])
    rows = list(await state["runtime"].broker.positions(account_id))
    state["positions_cache"][account_id] = {"loaded_at": now, "rows": rows}
    return rows


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=ZoneInfo("UTC"))


def _upsert_runtime_assignments(state: dict[str, Any], configuration: dict[str, Any]) -> None:
    bindings = {
        str(row.get("account_key") or ""): row
        for row in dict(configuration.get("accounts") or {}).get("bindings") or []
    }
    existing = {row.assignment_id for row in state["strategy"].assignments()}
    for payload in configuration.get("assignments") or []:
        if str(payload.get("assignment_id") or "") in existing:
            continue
        assignment = _assignment(payload, bindings, configuration)
        state["strategy"].upsert_assignment(assignment)
        state["planner"].upsert_instrument(
            InstrumentContract(
                instrument_id=f"ibkr:{assignment.conid}",
                conid=assignment.conid,
                symbol=assignment.ticker,
                security_type="STK",
                currency="USD",
                exchange="SMART",
            )
        )
        trading_journal().save_strategy_assignment(assignment.payload())


def _assignment(
    payload: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
    configuration: dict[str, Any],
) -> StrategyAssignment:
    account_key = str(payload.get("account_key") or "")
    binding = bindings.get(account_key) or {}
    account_id = str(binding.get("source_account_id") or "").strip()
    if not account_id:
        raise ValueError(f"Strategy assignment account {account_key} has no broker id")
    ticker = str(payload.get("ticker") or "").upper()
    side = str(payload.get("side") or "long")
    state = campaign_state(
        campaign_id=str(payload.get("campaign_id") or f"{configuration['run_plan']['run_plan_id']}:{ticker}:{side}"),
        deployment_id=str(payload.get("deployment_id") or configuration["run_plan"]["run_plan_id"]),
        profile_id=str(payload.get("profile_id") or configuration["strategy"].get("profile_id") or ""),
        book_id=str(payload.get("book_id") or configuration["run_plan"].get("book_id") or "default"),
        universe_id=str(payload.get("universe_id") or configuration["run_plan"].get("universe_id") or ""),
        side=side,
    )
    state["campaign_policy"] = deepcopy(
        dict(payload.get("campaign_policy") or configuration.get("campaign_policy") or {})
    )
    return StrategyAssignment(
        assignment_id=str(payload["assignment_id"]),
        strategy_id=str(payload.get("strategy_id") or configuration["strategy"]["strategy_id"]),
        strategy_revision=int(payload.get("strategy_revision") or configuration["strategy"]["revision"]),
        account_id=account_id,
        ticker=ticker,
        conid=int(payload.get("conid") or 0),
        status=AssignmentStatus(str(payload.get("status") or "watching")),
        permissions=StrategyPermissions(**dict(payload.get("permissions") or {})),
        parameters=dict(payload.get("resolved_parameters") or {}),
        state=state,
        source=str(payload.get("source") or "signal_stream_runtime"),
        created_at=datetime.now(tz=ZoneInfo("UTC")),
        updated_at=datetime.now(tz=ZoneInfo("UTC")),
    )


LIVE_STRATEGY_RUNTIME = LiveStrategyRuntimeSupervisor()

from __future__ import annotations

import unittest
import asyncio
import datetime as dt
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import torch

from research.bar_gpt.v3.data import BarView
from research.bar_gpt.v3.schema import FEATURE_NAMES

from bar_gpt_service.cache import CALENDAR_VIEWS, INTRADAY_VIEW_US, CausalCache
from bar_gpt_service.contracts import ScopeRequest
from bar_gpt_service.runtime import BarGptRuntime, PrioritySemaphore
from bar_gpt_service.sources import HistoricalBootstrap


def _runtime() -> BarGptRuntime:
    config = SimpleNamespace(
        prediction_history=8,
        queue_capacity=8,
        warm_concurrency=2,
        maximum_tickers=10,
        minimum_warm_1s_bars=1,
        runtime_root=Path("unused-runtime"),
    )
    runtime = BarGptRuntime(config)
    runtime.releases = {
        "v2-fixed": SimpleNamespace(config=SimpleNamespace(model_id="v2-fixed", version="v2", role="champion")),
        "v3-fixed": SimpleNamespace(config=SimpleNamespace(model_id="v3-fixed", version="v3", role="shadow")),
    }
    return runtime


class RuntimeContractTests(unittest.TestCase):
    def test_interactive_warm_has_priority_over_queued_live_warm(self) -> None:
        async def scenario() -> list[str]:
            gate = PrioritySemaphore(1)
            holder_entered = asyncio.Event()
            release_holder = asyncio.Event()
            order: list[str] = []

            async def worker(name: str, priority: int, *, holder: bool = False) -> None:
                async with gate.slot(priority):
                    if holder:
                        holder_entered.set()
                        await release_holder.wait()
                    else:
                        order.append(name)

            holder = asyncio.create_task(worker("holder", 1, holder=True))
            await holder_entered.wait()
            queued_live = asyncio.create_task(worker("live", 1))
            await asyncio.sleep(0)
            interactive = asyncio.create_task(worker("replay", 0))
            await asyncio.sleep(0)
            release_holder.set()
            await asyncio.gather(holder, queued_live, interactive)
            return order

        self.assertEqual(asyncio.run(scenario()), ["replay", "live"])

    def test_background_warm_cannot_consume_interactive_capacity(self) -> None:
        async def scenario() -> tuple[int, bool]:
            gate = PrioritySemaphore(4, background_limit=1)
            release = asyncio.Event()
            first_live_entered = asyncio.Event()
            second_live_entered = asyncio.Event()
            replay_entered = asyncio.Event()

            async def worker(priority: int, entered: asyncio.Event) -> None:
                async with gate.slot(priority):
                    entered.set()
                    await release.wait()

            first_live = asyncio.create_task(worker(1, first_live_entered))
            await first_live_entered.wait()
            second_live = asyncio.create_task(worker(1, second_live_entered))
            replay = asyncio.create_task(worker(0, replay_entered))
            await asyncio.wait_for(replay_entered.wait(), timeout=1)
            await asyncio.sleep(0)
            active_live = int(first_live_entered.is_set()) + int(second_live_entered.is_set())
            release.set()
            await asyncio.gather(first_live, second_live, replay)
            return active_live, replay_entered.is_set()

        self.assertEqual(asyncio.run(scenario()), (1, True))

    def _scoped_runtime(self, warm_status: str) -> BarGptRuntime:
        runtime = _runtime()
        runtime.caches["live"] = SimpleNamespace(
            readiness=lambda *_args: {"ready": warm_status == "ready"}, summary=lambda: {}
        )
        runtime.scopes["watchlist"] = {
            "cache_id": "live",
            "expires_monotonic": time.monotonic() + 60,
            "request": {
                "mode": "live", "trigger_mode": "manual", "tickers": ["AAPL"],
                "clock_us": None, "model_ids": ["v2-fixed"],
            },
        }
        runtime._warm_state[("live", "AAPL")] = {"status": warm_status}
        return runtime

    def test_scope_preserves_rank_order_and_deduplicates(self) -> None:
        request = ScopeRequest(mode="live", tickers=["MSFT", "aapl", "MSFT"])
        self.assertEqual(request.tickers, ["MSFT", "AAPL"])

    def test_model_aliases_resolve_to_immutable_release_ids(self) -> None:
        runtime = _runtime()
        self.assertEqual(runtime._resolve_model_ids(["bar_gpt_v3", "v2"]), ["v3-fixed", "v2-fixed"])

    def test_automatic_plan_keeps_champion_on_critical_path_and_bounds_shadow(self) -> None:
        runtime = _runtime()
        runtime._shadow_sample_rate = 1.0
        runtime._shadow_max_tickers = 2
        plan = runtime._inference_plan(
            ["v3-fixed"], ["A", "B", "C"], 1_000_000, automatic=True
        )
        self.assertEqual(plan[0], ("v2-fixed", ["A", "B", "C"]))
        self.assertEqual(plan[1], ("v3-fixed", ["A", "B"]))

    def test_explicit_shadow_scope_runs_requested_shadow_beside_champion(self) -> None:
        runtime = _runtime()
        runtime._shadow_sample_rate = 0.0
        runtime._shadow_max_tickers = 4
        plan = runtime._inference_plan(["v3-fixed"], ["AAPL"], 1_000_000, automatic=True)
        self.assertEqual(plan, [("v2-fixed", ["AAPL"]), ("v3-fixed", ["AAPL"])])

    def test_health_reports_scope_warming_and_failed(self) -> None:
        with patch("bar_gpt_service.runtime.release_summary", return_value={"model_id": "v2-fixed"}):
            warming = self._scoped_runtime("warming").health()
        self.assertEqual(warming["status"], "warming")
        self.assertEqual(warming["warm"]["warming"], 1)
        with patch("bar_gpt_service.runtime.release_summary", return_value={"model_id": "v2-fixed"}):
            failed = self._scoped_runtime("failed").health()
        self.assertEqual(failed["status"], "degraded")
        self.assertEqual(failed["warm"]["failed"], 1)

    def test_event_log_failure_is_visible_in_health_and_metrics(self) -> None:
        runtime = _runtime()
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            runtime._record_event("test", {})
        with patch("bar_gpt_service.runtime.release_summary", return_value={"model_id": "v2-fixed"}):
            health = runtime.health()
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["event_log"]["status"], "failed")
        self.assertEqual(health["metrics"]["event_log_write_failures"], 1)

    def test_automatic_schedule_coalesces_while_warming_then_admits_latest(self) -> None:
        runtime = _runtime()
        ready = {"value": False}
        runtime.caches["live"] = SimpleNamespace(
            readiness=lambda *_args: {"ready": ready["value"]}, summary=lambda: {}
        )
        runtime.scopes["watchlist"] = {
            "cache_id": "live",
            "expires_monotonic": time.monotonic() + 60,
            "request": {
                "mode": "live", "trigger_mode": "auto", "tickers": ["AAPL"],
                "clock_us": None, "model_ids": ["v2-fixed"],
            },
        }
        runtime._schedule_auto("live", "AAPL", 1_000_000)
        runtime._schedule_auto("live", "AAPL", 2_000_000)
        self.assertTrue(runtime._queue.empty())
        self.assertEqual(runtime._deferred_auto[("live", "AAPL")], 2_000_000)
        self.assertEqual(len(runtime.failures), 0)
        ready["value"] = True
        runtime._schedule_auto("live", "AAPL", 1_500_000)
        self.assertEqual(runtime._queue.get_nowait(), ("live", "AAPL", 2_000_000))
        self.assertNotIn(("live", "AAPL"), runtime._deferred_auto)

    def test_warm_admission_is_chunked_and_yields_to_health_loop(self) -> None:
        runtime = _runtime()
        calls: list[int] = []

        def admit(rows, *, derive=True):
            self.assertFalse(derive)
            calls.append(len(rows))
            time.sleep(0.01)

        runtime.caches["live"] = SimpleNamespace(upsert_many=admit)

        async def exercise() -> int:
            ticks = 0
            running = True

            async def health_loop() -> None:
                nonlocal ticks
                while running:
                    ticks += 1
                    await asyncio.sleep(0)

            health_task = asyncio.create_task(health_loop())
            await runtime._admit_warm_rows("live", [SimpleNamespace()] * 5_000)
            running = False
            await health_task
            return ticks

        ticks = asyncio.run(exercise())
        self.assertEqual(calls, [2_048, 2_048, 904])
        self.assertGreater(ticks, 2)

    def test_real_warm_path_materializes_calendar_and_saves_snapshot(self) -> None:
        runtime = _runtime()
        capacities = {view: 2 for view in (*INTRADAY_VIEW_US, *CALENDAR_VIEWS)}
        runtime.caches["live"] = CausalCache(capacities, raw_capacity_1s=4, raw_capacity_1d=4)
        data = SimpleNamespace(
            identity_database="identity", identity_interval_table="intervals",
            identity_entity_table="entities", identity_event_table="events",
            split_database="reference", split_table="splits",
            intraday_context_by_name={view: 1 for view in INTRADAY_VIEW_US},
            calendar_context_by_name={view: 1 for view in CALENDAR_VIEWS},
            intraday_warmup_bars_1s=2, calendar_warmup_daily_bars=2,
            clickhouse_prefetch_pages=1,
        )
        release = SimpleNamespace(
            config=SimpleNamespace(model_id="v2-fixed", version="v2", role="champion"),
            data_config=data,
        )
        runtime.releases = {"v2-fixed": release}
        actual = object.__new__(HistoricalBootstrap)
        actual.release = release
        actual.materialized = SimpleNamespace(
            read_identity_intervals=lambda *_args, **_kwargs: {"AAPL": []},
            read_split_actions=lambda *_args, **_kwargs: {"AAPL": []},
        )
        start_us = 1_000_000
        daily_view = BarView(
            features=torch.zeros((1, len(FEATURE_NAMES)), dtype=torch.float32),
            bar_start_us=torch.tensor([start_us], dtype=torch.long),
            bar_end_us=torch.tensor([start_us + 1_000_000], dtype=torch.long),
            available_at_us=torch.tensor([start_us + 1_000_000], dtype=torch.long),
        )
        actual.direct = SimpleNamespace(
            iter_session_views=lambda **_kwargs: [],
            iter_daily_views=lambda **_kwargs: [("1970-01-01", daily_view, 1)],
        )
        revision = {"revision_sha256": "a" * 64}
        harness = SimpleNamespace(
            source_revision=MagicMock(return_value=revision),
            load=lambda ticker, as_of, *, include_calendar=True, stop_requested=None: HistoricalBootstrap.load(
                actual,
                ticker,
                as_of,
                include_calendar=include_calendar,
                stop_requested=stop_requested,
            ),
        )
        snapshot_store = SimpleNamespace(load=MagicMock(return_value=[]), save=MagicMock())
        runtime._snapshot_store = snapshot_store
        with (
            patch("bar_gpt_service.runtime.HistoricalBootstrap", return_value=harness),
            patch.object(runtime, "_record_event"),
        ):
            asyncio.run(runtime._warm("live", "AAPL", 3_000_000))
        self.assertEqual(runtime._warm_state[("live", "AAPL")]["status"], "ready")
        self.assertEqual(harness.source_revision.call_count, 2)
        snapshot_store.save.assert_called_once()
        self.assertTrue(snapshot_store.save.call_args.args[2])

    def test_replay_warm_reuses_and_refreshes_same_session_snapshot(self) -> None:
        runtime = _runtime()
        runtime.caches["replay"] = MagicMock()
        release = runtime.releases["v2-fixed"]
        release.data_config = SimpleNamespace()
        revision = {"revision_sha256": "b" * 64}
        snapshot_row = SimpleNamespace(available_at_us=1)
        snapshot_store = SimpleNamespace(
            load=MagicMock(return_value=[snapshot_row]),
            save=MagicMock(),
        )
        runtime._snapshot_store = snapshot_store
        harness = SimpleNamespace(
            source_revision=MagicMock(return_value=revision),
            load=MagicMock(return_value=[]),
        )
        runtime._admit_warm_rows = MagicMock(
            side_effect=lambda *_args: asyncio.sleep(0)
        )
        runtime.caches["replay"].snapshot_rows.return_value = [snapshot_row]
        with (
            patch("bar_gpt_service.runtime.HistoricalBootstrap", return_value=harness),
            patch.object(runtime, "_record_event"),
        ):
            asyncio.run(runtime._warm("replay", "AAPL", 3_000_000))
        snapshot_store.load.assert_called_once_with("AAPL", 3_000_000, revision)
        self.assertFalse(harness.load.call_args.kwargs["include_calendar"])
        snapshot_store.save.assert_called_once()

    def test_historical_warm_stops_before_queries_when_shutdown_is_requested(self) -> None:
        bootstrap = object.__new__(HistoricalBootstrap)
        bootstrap.release = SimpleNamespace(data_config=SimpleNamespace())
        bootstrap.materialized = MagicMock()
        with self.assertRaisesRegex(InterruptedError, "cancelled for service shutdown"):
            bootstrap.load(
                "AAPL",
                dt.datetime.now(dt.UTC),
                stop_requested=lambda: True,
            )
        bootstrap.materialized.read_identity_intervals.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from src.backend import historical_signal_preparation as service


class SignalPreparationTests(IsolatedAsyncioTestCase):
    async def test_configured_reconstruction_reuses_certified_frozen_population(self):
        stream = dict(signal_stream_id='early', occurrence_source='qmd_squeeze_episode')
        config = dict(signal_activation=dict(rule_sets=[], column_catalog=[]))
        with patch.object(service.asyncio, 'create_subprocess_exec', side_effect=self.fake_producer) as producer:
            first = await service.reconstruct_configured_signal_occurrences(stream, configuration=config,
                start=self.start, end=self.end)
            self.population.side_effect = AssertionError('Cached certification must not query reference data')
            second = await service.reconstruct_configured_signal_occurrences(stream, configuration=config,
                start=self.start, end=self.end)
        self.assertEqual(first, second)
        self.assertEqual(producer.call_count, 1)

    async def test_configured_reconstruction_pins_exact_rules_without_price_or_common_share_filter(self):
        from unittest.mock import AsyncMock
        stream = dict(signal_stream_id='early', occurrence_source='qmd_squeeze_episode',
                      inclusion_rule_sets=['rule'], exclusion_rule_sets=[])
        rule = dict(rule_set_id='rule', conditions=[dict(value=123)])
        config = dict(signal_activation=dict(rule_sets=[rule, dict(rule_set_id='unrelated')], column_catalog=[]))
        execute = AsyncMock(return_value={'occurrences': []})
        with patch.object(service, '_execute_plans', execute):
            await service.reconstruct_configured_signal_occurrences(stream, configuration=config,
                start=self.start, end=self.end)
        plan = execute.call_args.args[1][0]
        self.assertIsNone(plan['recipe']['maximum_price_exclusive'])
        self.assertEqual(plan['recipe']['configuration']['streams'], [stream])
        self.assertEqual(plan['recipe']['configuration']['rule_sets'], [rule])
        self.population.assert_called_with(self.start.date(), common_only=False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, TRADINGML_RUNTIME_ROOT=str(self.root))
        env.start()
        self.addCleanup(env.stop)
        self.seed = self.root / "seed"
        (self.seed / "signals").mkdir(parents=True)
        self.start = datetime(2026, 8, 21, 8, tzinfo=UTC)
        self.end = self.start + timedelta(hours=16)
        self.stream = dict(signal_stream_id="early", occurrence_source="qmd_squeeze_episode")
        self.request = dict(configuration=dict(streams=[self.stream.copy()], rule_sets=[{"id": "fixed-rule"}],
            column_catalog=[], session_start_utc=self.start.isoformat(), session_end_utc=self.end.isoformat()),
            tickers=["OLD"], maximum_price_exclusive=20.0,
            population_authority=dict(table="q_live.feature_tradable_universe_v1",
                classification_table="q_live.id_symbol_v1", accepted_types=["CS", "ADRC"]))
        service._save(self.seed / "request.json", self.request)
        self.write_artifact(self.seed / "signals", self.seed / "request.json", "OLD")
        self.stream["historical_occurrence_artifact"] = dict(manifest_path=str(self.seed / "signals/manifest.json"),
            manifest_sha256=service._hash(self.seed / "signals/manifest.json"))
        self.original = deepcopy(self.stream)
        self.binary = self.root / "producer.exe"
        self.binary.write_bytes(b"test-only")
        pop = patch.object(service, "_population", return_value=([dict(ticker="NEW")], []))
        self.population = pop.start()
        self.addCleanup(pop.stop)
        self.real_binary = service._binary
        binary = patch.object(service, "_binary", return_value=self.binary)
        binary.start()
        self.addCleanup(binary.stop)

    def write_artifact(self, directory, request_path, ticker="NEW"):
        directory.mkdir(parents=True, exist_ok=True)
        request = json.loads(request_path.read_bytes())
        config = request["configuration"]
        event = dict(ticker=ticker, event_id=config["session_start_utc"] + ticker,
                     signal_stream_id="early", available_at=config["session_start_utc"], event_time=config["session_start_utc"])
        (directory / "occurrences.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        manifest = dict(schema_version=1, complete=True, authority="qmd_canonical_sip_squeeze_replay_v1",
            source_revision=dict(complete_for_history=True, request_complete=True),
            available_start=config["session_start_utc"], available_end=config["session_end_utc"],
            request_sha256=service._hash(request_path), stream_definitions=config["streams"],
            occurrences_sha256=service._hash(directory / "occurrences.jsonl"), row_count=1,
            maximum_price_exclusive=20.0, timing_policy="canonical_sip", activation="first_qualifying_signal_through_session_end")
        service._save(directory / "manifest.json", manifest)

    async def fake_producer(self, binary, request, output, **kwargs):
        self.write_artifact(Path(output), Path(request))
        class Process:
            returncode = 0
            async def wait(self):
                return 0
        return Process()

    async def test_original_session_reuses_exact_pin_without_population_or_producer(self):
        with patch.object(service.asyncio, "create_subprocess_exec") as producer:
            result = await service.prepared_signal_occurrences(self.stream, start=self.start, end=self.end)
        producer.assert_not_called()
        self.population.assert_not_called()
        self.assertEqual(result["occurrences"][0]["ticker"], "OLD")
        self.assertEqual(self.stream, self.original)

    async def test_new_dates_generate_distinct_certified_requests_and_reuse(self):
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=self.fake_producer) as producer:
            for offset in (-2, 3):
                start, end = self.start + timedelta(days=offset), self.end + timedelta(days=offset)
                check = service.signal_coverage_check([self.stream], start=start, end=end)
                self.assertEqual(check["evidence"]["preparation_sessions"], 1)
                self.assertEqual(producer.call_count, 0 if offset == -2 else 1)
                first = await service.prepared_signal_occurrences(self.stream, start=start, end=end)
                second = await service.prepared_signal_occurrences(self.stream, start=start, end=end)
                self.assertEqual(first, second)
                self.assertEqual(first["occurrences"][0]["ticker"], "NEW")
                request = next(p for p in (self.root / "trading/signal-preparation").rglob("request.json")
                               if start.date().isoformat() in str(p))
                saved = json.loads(request.read_bytes())
                self.assertEqual(saved["configuration"]["rule_sets"], self.request["configuration"]["rule_sets"])
                self.assertEqual(saved["tickers"], ["NEW"])
            self.assertEqual(producer.call_count, 2)
        self.assertEqual(self.stream, self.original)

    async def test_concurrent_callers_share_one_producer(self):
        start, end = self.start + timedelta(days=3), self.end + timedelta(days=3)
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=self.fake_producer) as producer:
            results = await asyncio.gather(*(service.prepared_signal_occurrences(self.stream, start=start, end=end) for _ in range(2)))
        self.assertEqual(producer.call_count, 1)
        self.assertEqual(results[0], results[1])

    async def test_corrupt_pin_and_recipe_fail_closed(self):
        start, end = self.start + timedelta(days=3), self.end + timedelta(days=3)
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=self.fake_producer):
            await service.prepared_signal_occurrences(self.stream, start=start, end=end)
        manifest = next((self.root / "trading/signal-preparation").rglob("manifest.json"))
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with self.assertRaisesRegex(RuntimeError, "hash changed"):
            await service.prepared_signal_occurrences(self.stream, start=start, end=end)
        (self.seed / "request.json").write_text("{}")
        check = service.signal_coverage_check([self.stream], start=start, end=end)
        self.assertEqual(check["status"], "blocked")
        self.assertIn("request hash changed", check["summary"])

    async def test_missing_population_blocks_preflight_without_creating_run_artifacts(self):
        self.population.side_effect = ValueError("No published population")
        check = service.signal_coverage_check([self.stream], start=self.start + timedelta(days=3), end=self.end + timedelta(days=3))
        self.assertEqual(check["status"], "blocked")
        self.assertFalse((self.root / "trading").exists())

    async def test_multiple_sessions_keep_both_occurrences_for_same_ticker(self):
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=self.fake_producer):
            result = await service.prepared_signal_occurrences(self.stream, start=self.start - timedelta(days=2),
                                                               end=self.end - timedelta(days=1))
        self.assertEqual(len(result["occurrences"]), 2)
        self.assertEqual(len(result["authority"]["sessions"]), 2)
        self.assertNotEqual(*[event["event_id"] for event in result["occurrences"]])

    async def test_stop_terminates_producer_and_never_publishes_partial_artifact(self):
        start, end = self.start + timedelta(days=3), self.end + timedelta(days=3)
        process_done = asyncio.Event()
        class Process:
            returncode = None
            terminated = False
            async def wait(self):
                await process_done.wait()
                return -1
            def terminate(self):
                self.terminated = True
                self.returncode = -1
                process_done.set()
        process = Process()
        async def launch(*args, **kwargs):
            return process
        checks = iter([False, True])
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=launch):
            with self.assertRaises(asyncio.CancelledError):
                await service.prepared_signal_occurrences(self.stream, start=start, end=end, stopped=lambda: next(checks))
        self.assertTrue(process.terminated)
        self.assertFalse(list((self.root / "trading").rglob("artifact.json")))
        self.assertEqual(len(list((self.root / "trading").rglob("plan.json"))), 1)

    async def test_controller_persists_new_session_occurrences_and_authority(self):
        from test_replay_run_service import approved_configuration
        from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition
        from src.trading_runtime.journal import TradingJournal
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = dict(signal_streams=[self.stream])
        controller = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 8, 19),
            start_time=service.time(4), configuration_revision=configuration), runtime_root=self.root / "runs")
        controller.run_dir.mkdir(parents=True, exist_ok=True)
        controller._journal = TradingJournal(controller.run_dir / "journal.sqlite3")
        try:
            with patch.object(service.asyncio, "create_subprocess_exec", side_effect=self.fake_producer) as producer:
                events = await controller._load_source_native_signal_events()
                repeated = await controller._load_source_native_signal_events()
            self.assertEqual(producer.call_count, 1)
            self.assertEqual([event.ticker for event in events], ["NEW"])
            self.assertEqual(events[0].available_at.date(), date(2026, 8, 19))
            self.assertEqual(events, repeated)
            records = list(controller._journal.records(controller.run_id))
            self.assertEqual(len([row for row in records if row.category == "market_discovery_signal"]), 1)
            self.assertTrue(controller._data_authority)
        finally:
            controller._journal.close()

    async def test_backtest_preflight_blocks_missing_signal_coverage(self):
        from test_replay_run_service import approved_configuration
        from src.backend.replay_run_service import backtest_preflight
        configuration = approved_configuration()
        configuration["payload"]["signal_activation"] = dict(signal_streams=[self.stream])
        configuration["payload"]["run_plan"] = dict(signal_stream_ids=["early"],
            activation=dict(watchlist_policy="not_required", watch_duration="session"))
        self.population.side_effect = ValueError("No published population for 2026-08-24")
        with patch("src.backend.replay_run_service.historical_preflight", return_value=dict(
            checks=[], strategy_run_ready=True, window=dict(sessions=["2026-08-24"]))), \
             patch("src.backend.replay_run_service.qmd_history_get_json", return_value={}), \
             patch("src.backend.replay_run_service.runtime_version_check", return_value=dict(status="ready")), \
             patch("src.backend.replay_run_service.backtest_runtime_root", return_value=self.root):
            result = backtest_preflight(anchor_date=date(2026, 8, 25), session_count=1,
                                         configuration_revision=configuration)
        self.assertFalse(result["strategy_run_ready"])
        check = next(row for row in result["checks"] if row["id"] == "historical_signal_coverage")
        self.assertEqual(check["status"], "blocked")
        self.assertIn("2026-08-24", check["summary"])

    async def test_outdated_producer_is_rejected_before_work(self):
        with patch.dict(os.environ, QMD_HISTORICAL_SIGNAL_PRODUCER=str(self.binary)), \
             patch.object(service.subprocess, "run", return_value=SimpleNamespace(returncode=0,
                 stdout=json.dumps(dict(source_sha256="stale")))):
            with self.assertRaisesRegex(ValueError, "outdated"):
                self.real_binary()

    async def test_interrupted_request_is_reused_and_changed_request_is_rejected(self):
        start, end = self.start + timedelta(days=3), self.end + timedelta(days=3)
        plan = service.signal_session_plans(self.stream, start=start, end=end)[0]
        request = service._freeze_request(plan)
        original = request.read_bytes()
        # Resume must retain the frozen population even if a subsequent query differs.
        plan["population"] = [dict(ticker="CHANGED")]
        service._freeze_request(plan)
        self.assertEqual(original, request.read_bytes())
        request.write_bytes(original + b" ")
        with patch.object(service.asyncio, "create_subprocess_exec") as producer:
            with self.assertRaisesRegex(ValueError, "request changed"):
                await service.prepared_signal_occurrences(self.stream, start=start, end=end)
            producer.assert_not_called()

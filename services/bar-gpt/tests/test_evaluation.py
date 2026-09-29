from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch

from bar_gpt_service.cache import RawBar, CausalCache
from bar_gpt_service.evaluation import capacity_summary, configuration, implementation_hash, replay
from bar_gpt_service.evaluation_data import clock, make_packet, read_packet, validate_dataset
from bar_gpt_service.evaluation_score import Metrics, day_interval, score
from bar_gpt_service.evaluation_store import Results, atomic_json, bind_manifest, digest, gpu_preflight, file_hash
from research.bar_gpt.v3.schema import FEATURE_NAMES, FEATURE_INDEX
from research.bar_gpt.v3.targets import build_physical_horizon_targets

RUNTIME = Path(r"D:\TradingML\runtimes")


def bar(origin, price=100., ticker="TEST", view="1s"):
    values = [0.] * len(FEATURE_NAMES)
    for name in ("trade_present", "context_eligible", "origin_eligible", "trade_event_count", "trade_size_sum", "source_event_count"):
        values[FEATURE_INDEX[name]] = 1.
    for name in ("trade_open", "trade_high", "trade_low", "trade_close"):
        values[FEATURE_INDEX[name]] = price
    return RawBar(ticker, view, origin-1_000_000, origin, origin, tuple(values), 1, "test", "fixture")


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=RUNTIME, prefix="bargpt-eval-test-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_refuses_changed_population(self):
        path = self.root / "manifest.json"
        bind_manifest(path, {"tickers": ["A"]})
        bind_manifest(path, {"tickers": ["A"]})
        with self.assertRaises(RuntimeError):
            bind_manifest(path, {"tickers": ["B"]})

    def test_reconciliation_rejects_missing_duplicate_stale_and_wrong_hash(self):
        results = Results(self.root / "results.sqlite")
        correct = dict(ticker="A", model_id="m", event_at_us=100, checkpoint_hash="hash")
        for rows in ([], [correct, correct], [{**correct, "event_at_us": 99}], [{**correct, "checkpoint_hash": "bad"}]):
            with self.assertRaises(RuntimeError):
                results.record("day", 100, "m", ["A"], rows, "hash", .1, .1)
        self.assertEqual(results.existing("day", 100, "m"), set())
        results.record("day", 100, "m", ["A"], [correct], "hash", .1, .1)
        self.assertEqual(results.existing("day", 100, "m"), {"A"})
        results.close()

    def test_packet_native_targets_and_future_admission(self):
        start = clock("2026-08-03", "09:30:00")
        bars = [bar(start+i*1_000_000, 100+i/100) for i in range(70)]
        packet = make_packet(self.root / "packet.sqlite", bars, "TEST", "2026-08-03", start, start+10_000_000,
                             (5_000_000,), {"revision": "test"})
        db = read_packet(self.root / packet["file"])
        label = json.loads(db.execute("SELECT payload FROM targets WHERE origin=?", (start,)).fetchone()[0])
        native = build_physical_horizon_targets(torch.tensor([row.values for row in bars]), torch.tensor([0]),
                   torch.tensor([5_000_000]), available_at_us=torch.tensor([row.available_at_us for row in bars]),
                   coverage_end_us=bars[-1].available_at_us)
        self.assertEqual(label["values"], native.values[0].tolist())
        cache = CausalCache({"1s": 10}, 10)
        cache.upsert_many([RawBar(**json.loads(row[0])) for row in db.execute("SELECT payload FROM bars WHERE available<=?", (start,))], derive=False)
        self.assertEqual(len(cache.rows("TEST", "1s", start)), 1)
        self.assertEqual(cache.rows("TEST", "1s", start)[0].values[FEATURE_INDEX["trade_close"]], 100.)
        db.close()

    def test_perfect_forecast_and_negative_skill(self):
        good = Metrics()
        bad = Metrics()
        for truth in (-.1, 0., .1):
            good.update([truth, truth, truth], truth, [.1,.5,.9], None)
            bad.update([1.,1.,1.], truth, [.1,.5,.9], None)
        self.assertEqual(good.result()["mae_bps"], 0.)
        self.assertEqual(good.result()["mcc"], 1.)
        self.assertLess(bad.result()["skill_vs_zero"], 0.)
        self.assertIsNone(day_interval([1,2])["confidence_interval"])

    def test_paced_lag_is_not_service_duration(self):
        results = Results(self.root / "results.sqlite")
        for index in range(3):
            prediction = dict(ticker="A", model_id="m", event_at_us=index, checkpoint_hash="h")
            results.record("day", index, "m", ["A"], [prediction], "h", .5, 2.)
        summary = capacity_summary(results, True, 1., 0)
        self.assertEqual(summary["deadline_misses"], 3)
        self.assertFalse(summary["live_certified"])
        results.close()

    def test_gpu_preflight_blocks_training_and_unknown_process(self):
        for process in ("123, C:\\python.exe", "777, [Insufficient Permissions]"):
            with patch("subprocess.run", side_effect=[SimpleNamespace(stdout=process), SimpleNamespace(stdout="0, GPU, 0, 100, 90000")]):
                with self.assertRaises(RuntimeError):
                    gpu_preflight()

    def test_gpu_preflight_allows_only_system_logonui_and_retains_load_limits(self):
        import os
        genuine = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "LogonUI.exe")
        for process, utilization, allowed in ((genuine, 0, True), (r"D:\other\LogonUI.exe", 0, False),
                                                (genuine, 90, False)):
            with patch("subprocess.run", side_effect=[SimpleNamespace(stdout=f"56252, {process}"),
                       SimpleNamespace(stdout=f"0, GPU, {utilization}, 906, 90000")]):
                if allowed:
                    self.assertIn("gpus", gpu_preflight())
                else:
                    with self.assertRaises(RuntimeError):
                        gpu_preflight()

    def test_score_reconciles_complete_packet_and_rejects_partial(self):
        day = "2026-08-03"
        start = clock(day, "09:30:00")
        bars = [bar(start+i*1_000_000, 100+i/100) for i in range(10)]
        packet = make_packet(self.root / "packet.sqlite", bars, "TEST", day, start, start+2_000_000,
                             (5_000_000,), {"revision": "test"})
        identity = bind_manifest(self.root / "plan.json", {"days": [day], "tickers": ["TEST"], "partition": "development"})
        dataset = {"identity": identity, "packets": [packet]}
        atomic_json(self.root / "dataset.json", dataset)
        bind_manifest(self.root / "experiment.json", {"dataset_hash": digest(dataset), "release": {"checkpoint_hash": "h"}})
        results = Results(self.root / "results.sqlite")
        with self.assertRaises(RuntimeError):
            score(self.root, self.root)
        db = read_packet(self.root / "packet.sqlite")
        for origin, payload in db.execute("SELECT origin,payload FROM targets"):
            target = json.loads(payload)
            prediction = dict(ticker="TEST", model_id="m", event_at_us=origin, checkpoint_hash="h",
                              raw={"horizon_quantiles": [[[x,x,x] for x in target["values"][0][:15]]]})
            results.record(day, origin, "m", ["TEST"], [prediction], "h", .1, .1)
        db.close()
        results.close()
        report = score(self.root, self.root)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["missing"], 0)

    def test_real_cpu_loader_runtime_replay_score_and_resume(self):
        from research.bar_gpt.v3 import LEARNING_CONTRACT
        from research.bar_gpt.v3.config import BarGPTConfig, DataConfig
        from research.bar_gpt.v3.model import BarGPTV3
        from research.bar_gpt.v3.inference import checkpoint_contract_hash
        torch.set_num_threads(2)
        torch.manual_seed(19)
        model_config = BarGPTConfig(d_model=32, n_layers=1, n_heads=4, n_kv_heads=2, horizon_rank=8)
        data = DataConfig()
        model = BarGPTV3(model_config)
        payload = {"model_family": "bar_gpt", "model_version": "v3", "learning_contract": LEARNING_CONTRACT,
                   "config": {"model": asdict(model_config), "data": asdict(data)}, "model": model.state_dict()}
        checkpoint = self.root / "fixture.pt"
        torch.save(payload, checkpoint)
        catalog = self.root / "releases.json"
        atomic_json(catalog, [{"model_id": "fixture", "version": "v3", "checkpoint": str(checkpoint),
                    "checkpoint_sha256": file_hash(checkpoint), "contract_hash": checkpoint_contract_hash(payload)}])
        day = "2026-08-03"
        start = clock(day, "09:30:00")
        code = implementation_hash()
        packets = []
        for ticker in ("AAA", "BBB"):
            bars = [bar(start+i*1_000_000, 100+i/100, ticker) for i in range(-64, 12)]
            for view in (*data.intraday_context_by_name, *data.calendar_context_by_name):
                if view != "1s":
                    bars.extend([bar(start-2_000_000, 100., ticker, view), bar(start-1_000_000, 101., ticker, view)])
            packets.append(make_packet(self.root / f"{ticker}.sqlite", bars, ticker, day, start, start+2_000_000,
                                       tuple(data.horizons_us), {"revision": "synthetic-test-only"}))
        identity = bind_manifest(self.root / "plan.json", {"days": [day], "tickers": ["AAA", "BBB"],
                      "start": "09:30:00", "end": "09:30:02", "partition": "development",
                      "data_config": asdict(data), "code_hash": code})
        atomic_json(self.root / "dataset.json", {"identity": identity, "packets": packets})
        run = self.root / "run"
        run.mkdir()
        args = SimpleNamespace(dataset=self.root, output=run, model_id="fixture", selection=None,
                               paced=False, steps=0, latency_seconds=1., max_backlog_seconds=10., parity=True)
        config = configuration(run, catalog, "fixture", "cpu", 2)
        result = asyncio.run(replay(args, config, code))
        self.assertEqual(result["predictions"], 4)
        result2 = asyncio.run(replay(args, config, code))
        self.assertEqual(result2["predictions"], 4)
        self.assertEqual(score(self.root, run)["status"], "complete")


if __name__ == "__main__":
    unittest.main()

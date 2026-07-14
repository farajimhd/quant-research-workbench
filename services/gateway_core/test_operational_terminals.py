from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from rich.console import Console

import services.gateway_core.rich_renderer as rich_renderer
from services.news_gateway.config import NewsGatewayConfig
from services.news_gateway.gateway import GatewayMetrics
from services.news_gateway.terminal import render_dashboard as render_news
from services.reference_gateway.config import ReferenceGatewayConfig, ReferenceGatewayConfigOverrides
from services.reference_gateway.daemon import ReferenceDaemonState
from services.reference_gateway.policy import evaluate_write_policy
from services.reference_gateway.state import ReferenceSourceState, ReferenceTableState
from services.reference_gateway.terminal import OperationRecord, ReferenceRunRecord, render_reference_dashboard
from services.sec_gateway.config import SecGatewayConfig
from services.sec_gateway.gateway import SecGatewayMetrics
from services.sec_gateway.terminal import render_dashboard as render_sec
from services.text_embed_gateway.config import TextEmbedGatewayConfig
from services.text_embed_gateway.gateway import TextEmbedGateway, TextEmbedMetrics
from services.text_embed_gateway.terminal import render_dashboard as render_text


class FakeGateway:
    pass


class OperationalTerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = str(Path.cwd())
        os.environ["TEXT_EMBED_GATEWAY_DATA_ROOT_WIN"] = root
        os.environ["REFERENCE_GATEWAY_DATA_ROOT_WIN"] = root

    def test_all_dashboards_fit_normal_and_compact_profiles(self) -> None:
        gateways = self._gateways()
        cases = (
            ("sec", lambda: render_sec(gateways["sec"]), "SEC Filing Pipeline"),
            ("news", lambda: render_news(gateways["news"], {"rows": [], "limit": 12}), "News Processing Pipeline"),
            ("text", lambda: render_text(gateways["text"]), "Embedding"),
            ("reference", lambda: render_reference_dashboard(gateways["reference"]), "Reference Integrity And Guardrails"),
        )
        for width, height, maximum in ((160, 44, 34), (100, 24, 24)):
            with patch.object(rich_renderer.shutil, "get_terminal_size", return_value=(width, height)):
                for name, render, required in cases:
                    with self.subTest(name=name, width=width, height=height):
                        output = render_text_output(render(), width=width, height=height)
                        self.assertLessEqual(len(output.splitlines()), maximum, output)
                        self.assertIn("Current Operation", output)
                        self.assertIn(required, output)

    def test_active_error_is_visible_and_resolved_error_clears_attention(self) -> None:
        gateway = self._gateways()["text"]
        gateway.metrics.failures = 1
        gateway.metrics.last_error = "ClickHouse connection reset"
        gateway.metrics.last_error_status = "active"
        with patch.object(rich_renderer.shutil, "get_terminal_size", return_value=(100, 24)):
            active = render_text_output(render_text(gateway), width=100, height=24)
            self.assertIn("Attention Required", active)
            self.assertIn("DEGRADED", active)
            self.assertLessEqual(len(active.splitlines()), 24, active)
            gateway.metrics.last_error_status = "resolved"
            gateway.metrics.last_error_resolved_at_utc = "2026-07-14T10:00:00Z"
            recovered = render_text_output(render_text(gateway), width=100, height=24)
            self.assertNotIn("Attention Required", recovered)
            self.assertIn("RUNNING", recovered)

    def test_text_error_recovery_requires_a_matching_mode(self) -> None:
        gateway = object.__new__(TextEmbedGateway)
        gateway.metrics = TextEmbedMetrics()
        gateway.logger = type("Logger", (), {"event": lambda *_args, **_kwargs: None})()
        gateway._record_error("SEC live failure", mode="live", source="sec")
        gateway._resolve_last_error(reason="historical_cycle_completed", mode="historical")
        self.assertEqual(gateway.metrics.last_error_status, "active")
        gateway._resolve_last_error(reason="live_cycle_completed", mode="live")
        self.assertEqual(gateway.metrics.last_error_status, "resolved")

    def test_reference_daemon_preserves_last_completed_child_snapshot(self) -> None:
        record = self._reference_record(run_mode="once")
        from services.reference_gateway.status import build_reference_status_snapshot

        snapshot = build_reference_status_snapshot(record)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.jsonl"
            path.write_text(json.dumps({"event": "standard_status_snapshot", "snapshot": snapshot}) + "\n", encoding="utf-8")
            state = ReferenceDaemonState(config=self._reference_config("daemon"), runtime_log_path=str(path))
            state.touch("running", "waiting_for_next_cycle", "Last child cycle completed successfully.")
            merged = state.dashboard_snapshot()
        self.assertEqual(merged["header"]["run_mode"], "daemon")
        self.assertEqual(len(merged["sources_sinks"]), len(record.source_states))
        self.assertEqual(merged["current_operation"]["phase"], "waiting_for_next_cycle")
        self.assertTrue(merged["service_specific"]["daemon_mode"])

    def test_reference_failed_run_controls_header_health(self) -> None:
        record = self._reference_record(run_mode="once")
        record.final_status = "failed"
        with patch.object(rich_renderer.shutil, "get_terminal_size", return_value=(100, 24)):
            output = render_text_output(render_reference_dashboard(record), width=100, height=24)
        self.assertIn("Reference Gateway  FAILED", output)

    def _gateways(self) -> dict[str, Any]:
        sec = FakeGateway()
        sec.config = SecGatewayConfig.from_env()
        sec.metrics = SecGatewayMetrics(
            current_phase="polling",
            preflight_status="ok",
            gap_status="ok",
            audit_status="ok",
            live_workers=4,
            live_active_workers=2,
            live_queue_size=2,
            live_worker_accessions={"1": "0001", "2": "0002"},
        )
        sec.snapshot_metrics = lambda: asdict(sec.metrics)
        sec.recent_snapshot = lambda limit=12: {"rows": [], "limit": limit}
        sec.current_poll_seconds = lambda: 2.0

        news = FakeGateway()
        news.config = NewsGatewayConfig.from_env()
        news.metrics = GatewayMetrics(
            current_phase="polling",
            preflight_status="ok",
            gap_status="ok",
            background_queue_size=3,
            background_active_batches=2,
            publish_status="running",
            publish_active_jobs=1,
        )
        news.snapshot_metrics = lambda: asdict(news.metrics)
        news.current_poll_strategy = lambda: type("Strategy", (), {"poll_seconds": 2.0, "lookback_minutes": 15, "session": "regular"})()

        text_gateway = FakeGateway()
        text_gateway.config = TextEmbedGatewayConfig.from_env()
        text_gateway.metrics = TextEmbedMetrics(
            current_phase="working",
            model_status="loaded",
            active_mode="historical",
            active_source="sec",
            active_stage="token_embed",
            active_detail="Embedding missing SEC token chunks.",
            source_reports={
                "live": {"news": {}, "sec": {}},
                "historical": {"news": {}, "sec": {"embedding_detected": 10, "embedding_completed": 4, "embedding_remaining": 6}},
            },
        )
        text_gateway.snapshot_metrics = lambda: asdict(text_gateway.metrics)
        text_gateway.recent_snapshot = lambda limit=12: {"rows": [], "limit": limit}
        return {"sec": sec, "news": news, "text": text_gateway, "reference": self._reference_record("once")}

    def _reference_config(self, run_mode: str) -> ReferenceGatewayConfig:
        return ReferenceGatewayConfig.from_env(
            ReferenceGatewayConfigOverrides(
                operator_mode="prod",
                run_mode=run_mode,
                integrity_mode="strict",
                maintenance_mode="auto",
                diagnostics_mode="none",
            )
        )

    def _reference_record(self, run_mode: str) -> ReferenceRunRecord:
        config = self._reference_config(run_mode)
        record = ReferenceRunRecord(config=config, write_policy=evaluate_write_policy(config))
        record.operations = [
            OperationRecord("Preflight", "completed", "Dependencies ready."),
            OperationRecord("Source: IBKR borrow availability", "running", "Refreshing borrow snapshots.", rows=800, seconds=9.4),
        ]
        record.source_states = [
            ReferenceSourceState("FINRA short volume", "stale", "through 2026-07-10", 1_000, "market_short_volume_v1", "overdue"),
            ReferenceSourceState("IBKR borrow snapshot", "ok", "current", 800, "market_security_borrow_v1", "latest completed"),
        ]
        record.table_states = [
            ReferenceTableState("identity", "ok", 3, 3, 12_000, "2026-07-14", ()),
            ReferenceTableState("canonical_security_facts", "partial", 4, 4, 9_000, "2026-07-14", ()),
        ]
        return record


def render_text_output(renderable: Any, *, width: int, height: int) -> str:
    stream = StringIO()
    Console(file=stream, force_terminal=False, color_system=None, width=width, height=height).print(renderable)
    return stream.getvalue()


if __name__ == "__main__":
    unittest.main()

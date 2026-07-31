from __future__ import annotations

import os
import io
import unittest
from unittest import mock

from rich.console import Console

from .config import IntelligenceConfig
from .terminal import render_dashboard


class TextIntelligenceTerminalTests(unittest.TestCase):
    def setUp(self) -> None:
        with (
            mock.patch("text_intelligence.config.load_repo_dotenv"),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            self.config = IntelligenceConfig.from_env()

    def test_normal_dashboard_prioritizes_workers_and_lifecycle(self) -> None:
        output = render_text(self.config, metrics(), width=160, height=50)

        self.assertIn("TEXT INTELLIGENCE", output.upper())
        self.assertIn("Current Worker Focus", output)
        self.assertIn("classifying", output)
        self.assertIn("Durable Lifecycle", output)
        self.assertIn("Recent Deterministic Work", output)
        self.assertLessEqual(len(output.splitlines()), 50)

    def test_compact_dashboard_keeps_current_work_and_failure_visible(self) -> None:
        payload = metrics()
        payload.update(
            {
                "status": "degraded",
                "current_phase": "degraded",
                "current_phase_message": "ClickHouse reconciliation timed out",
                "last_error": "TimeoutError: ClickHouse reconciliation timed out",
                "last_error_status": "active",
                "deterministic_reconcile_error_status": "active",
            }
        )

        output = render_text(self.config, payload, width=96, height=28)

        self.assertIn("DEGRADED", output)
        self.assertIn("TimeoutError", output)
        self.assertIn("Deterministic Work", output)
        self.assertIn("Current focus", output)
        self.assertNotIn("Durable Lifecycle", output)
        self.assertLessEqual(len(output.splitlines()), 28)


def metrics() -> dict[str, object]:
    return {
        "status": "running",
        "current_phase": "processing",
        "current_phase_message": "1 workers active; 3 notices queued.",
        "last_error": "",
        "last_error_status": "resolved",
        "deterministic_workers": [
            {
                "worker": 1,
                "status": "processing",
                "corpus": "news",
                "source_id": "840897bd1fdc945220c922951c2e2369",
                "stage": "classifying",
            },
            {
                "worker": 2,
                "status": "waiting",
                "corpus": "",
                "source_id": "",
                "stage": "waiting_for_notice",
            },
        ],
        "deterministic_queue_size": 3,
        "deterministic_pending": 4,
        "deterministic_active_workers": 1,
        "deterministic_reconcile_runs": 4,
        "deterministic_reconcile_notices": 7,
        "deterministic_reconcile_seconds": 0.42,
        "deterministic_reconcile_error_status": "resolved",
        "deterministic_queued": 12,
        "deterministic_reconciled": 7,
        "deterministic_completed": 8,
        "deterministic_skipped_current": 1,
        "deterministic_failed": 0,
        "deterministic_news_labels": 32,
        "deterministic_sec_labels": 14,
        "deterministic_live_forwarded": 0,
        "deterministic_live_forward_failed": 0,
        "deterministic_recent_work": [
            {
                "updated_at_utc": "2026-07-31T14:05:00Z",
                "corpus": "sec",
                "source_id": "0000000001-26-000001",
                "stage": "complete",
                "status": "complete",
                "detail": "",
            }
        ],
        "tasks": [],
        "enable_live_ai": False,
    }


def render_text(
    config: IntelligenceConfig,
    payload: dict[str, object],
    *,
    width: int,
    height: int,
) -> str:
    console = Console(
        width=width,
        height=height,
        record=True,
        file=io.StringIO(),
        force_terminal=False,
        color_system=None,
    )
    console.print(
        render_dashboard(
            config,
            payload,
            profile={
                "width": width,
                "height": height,
                "compact": height < 34,
                "narrow": width < 190,
                "roomy": width >= 190 and height >= 62,
            },
        )
    )
    return console.export_text()


if __name__ == "__main__":
    unittest.main()

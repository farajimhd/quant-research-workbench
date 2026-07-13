from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("qmd_terminal.py")
SPEC = importlib.util.spec_from_file_location("qmd_terminal_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
qmd = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qmd
SPEC.loader.exec_module(qmd)


def representative_state() -> qmd.PollState:
    now = datetime.now(UTC)
    state = qmd.PollState(base_url="http://127.0.0.1:8795", updated_at=now)
    lanes = [
        lane("massive_feed", "Massive feed", "healthy", required=True, success=now),
        lane("compact_events", "q_live.events persistence", "healthy", required=True, success=now),
        lane("intraday_bars", "Canonical intraday bars", "healthy", required=True, success=now),
        lane("coverage_ledger", "Live coverage ledger", "healthy", required=True, success=now),
        lane("compact_audit", "Compact-event warning audit", "healthy", required=True, success=now),
        lane("indicators", "Indicator persistence", "disabled", enabled=False),
        lane("live_market_state", "Abnormal market-state persistence", "healthy", required=True, success=now),
    ]
    operational = {"lanes": lanes, "recent_recoveries": []}
    state.health = {
        "status": "running",
        "session_phase": "Regular",
        "host_role": "laptop",
        "market_calendar": {
            "active_collection_window": True,
            "source": "massive",
            "reason": "regular session",
            "stale": False,
        },
        "config": {
            "clickhouse_database": "q_live",
            "compact_event_table": "events",
            "historical_clickhouse_database": "market_sip_compact",
            "replay_enabled": False,
        },
        "operational": operational,
    }
    state.status = {
        "header": {"status": "RUNNING", "host_role": "laptop"},
        "attention": [],
        "downstream_products": [
            {"product": "Intraday bars", "enabled": True, "state": "healthy", "rows": 500},
            {"product": "Indicators", "enabled": False, "state": "disabled"},
            {"product": "Scanner primitives", "enabled": True, "state": "healthy", "rows": 12},
            {"product": "Abnormal market state", "enabled": True, "state": "healthy", "rows": 2},
        ],
        "service_specific": {
            "operational": operational,
            "recent_sessions": ["2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13"],
        },
    }
    state.metrics = {
        "ingest_events": 100_000,
        "ingest_quotes": 60_000,
        "ingest_trades": 40_000,
        "last_event_lag_ms": 25,
        "compact_events_emitted": 99_995,
        "compact_events_persisted": 99_990,
        "compact_events_reorder_pending": 5,
        "compact_event_rejected": 0,
        "intraday_bar_rows_emitted": 520,
        "intraday_bar_rows_persisted": 500,
        "intraday_bar_repairs_requested": 2,
        "intraday_bar_repairs_completed": 2,
    }
    state.rates = {
        "ingest_quotes_per_sec": 1_200,
        "ingest_trades_per_sec": 350,
        "compact_events_emitted_per_sec": 1_550,
        "compact_events_persisted_per_sec": 1_548,
        "intraday_bar_rows_emitted_per_sec": 42,
        "intraday_bar_rows_persisted_per_sec": 40,
    }
    state.maintenance = {"active": False, "status": "up_to_date"}
    state.coverage = {
        "rows": [
            coverage("2026-07-10", "qmd_compact_event_writer", "compact_persisted"),
            coverage("2026-07-10", "qmd_intraday_bar_writer", "intraday_bars_persisted"),
            flatfile("2026-07-10", "quote", "remote_ready/confirmed"),
            flatfile("2026-07-10", "trade", "remote_ready/confirmed"),
        ]
    }
    for source in ("health", "status", "metrics", "maintenance", "coverage"):
        state.source_updated_at[source] = now
    return state


def lane(
    key: str,
    label: str,
    state: str,
    *,
    enabled: bool = True,
    required: bool = False,
    success: datetime | None = None,
) -> dict:
    return {
        "key": key,
        "label": label,
        "enabled": enabled,
        "required": required,
        "state": state,
        "detail": "healthy" if enabled else "disabled by configuration",
        "pending_rows": 0,
        "max_pending_rows": 0,
        "successful_rows": 100,
        "failures": 0,
        "last_success_utc": success.isoformat() if success else None,
    }


def coverage(session: str, source: str, status: str) -> dict:
    return {
        "table_group": "live_coverage",
        "start_ts_utc": f"{session}T08:00:00Z",
        "end_ts_utc": f"{session}T23:59:59Z",
        "action": source,
        "status": status,
    }


def flatfile(session: str, source: str, status: str) -> dict:
    return {
        "table_group": "flatfile_coverage",
        "start_ts_utc": f"{session}T00:00:00Z",
        "action": source,
        "status": status,
        "host_role": "laptop",
    }


class QmdTerminalTests(unittest.TestCase):
    def test_normal_and_compact_renders_fit_the_viewport(self) -> None:
        if qmd.Group is None:
            self.skipTest("Rich is unavailable")
        from rich.console import Console

        state = representative_state()
        for width, height in ((140, 44), (120, 40), (100, 24), (90, 30)):
            output = io.StringIO()
            with patch.object(qmd.shutil, "get_terminal_size", return_value=os.terminal_size((width, height))):
                Console(file=output, width=width, height=height, color_system=None).print(
                    qmd.render_dashboard(state)
                )
            lines = output.getvalue().splitlines()
            self.assertLessEqual(len(lines), height, f"{width}x{height} rendered {len(lines)} lines")
            self.assertIn("QMD Gateway", output.getvalue())
            self.assertIn("Attention / Required Action", output.getvalue())
            self.assertTrue("Live Event Pipeline" in output.getvalue() or "Operational Core" in output.getvalue())
            if width == 140:
                self.assertTrue(
                    any(
                        "Recent Live Coverage" in line and "Historical Sync" in line
                        for line in lines
                    ),
                    "wide coverage panels did not share a row",
                )

    def test_failed_poll_retains_last_good_snapshot_and_records_recovery(self) -> None:
        state = representative_state()
        good_health = dict(state.health)

        with patch.object(qmd, "fetch_json", side_effect=RuntimeError("service unavailable")):
            qmd.poll_once(state, [], 1, 0.1, details=False)
        self.assertEqual(state.health, good_health)
        self.assertEqual(set(state.poll_issues), {"health", "status", "metrics", "maintenance", "coverage"})

        responses = {
            "/health": state.health,
            "/snapshot/status": state.status,
            "/metrics": state.metrics,
            "/snapshot/maintenance": state.maintenance,
            "/snapshot/coverage?limit=40": state.coverage,
        }

        def fetch(url: str, _timeout: float):
            return responses[next(path for path in responses if url.endswith(path))]

        state.last_coverage_poll = 0
        with patch.object(qmd, "fetch_json", side_effect=fetch):
            qmd.poll_once(state, [], 1, 0.1, details=False)
        self.assertFalse(state.poll_issues)
        self.assertEqual(len(state.recoveries), 5)

    def test_plain_summary_is_bounded_human_output(self) -> None:
        line = qmd.plain_summary(representative_state())
        self.assertIn("status=running", line)
        self.assertIn("q_live=healthy", line)
        self.assertNotIn("{", line)

    def test_degraded_and_active_maintenance_renders_fit(self) -> None:
        if qmd.Group is None:
            self.skipTest("Rich is unavailable")
        from rich.console import Console

        state = representative_state()
        compact = qmd.operational_lanes(state)["compact_events"]
        compact.update(
            {
                "state": "failed",
                "detail": "ClickHouse insert timed out; batch retained for retry.",
                "pending_rows": 10_000,
                "failures": 2,
            }
        )
        state.status["header"]["status"] = "DEGRADED"
        state.status["attention"] = [
            {
                "severity": "critical",
                "area": "q_live.events persistence",
                "message": compact["detail"],
                "action": "Restore ClickHouse connectivity; pending rows remain buffered.",
            }
        ]
        state.maintenance = {
            "active": True,
            "status": "running",
            "phase": "recent_live_repair",
            "message": "Repairing the T-1 compact/bar coverage gap.",
            "completed_jobs": 4,
            "total_jobs": 10,
        }
        for width, height in ((140, 44), (100, 24)):
            output = io.StringIO()
            with patch.object(qmd.shutil, "get_terminal_size", return_value=os.terminal_size((width, height))):
                Console(file=output, width=width, height=height, color_system=None).print(
                    qmd.render_dashboard(state)
                )
            self.assertLessEqual(len(output.getvalue().splitlines()), height)
            self.assertIn("q_live.events", output.getvalue())
            if width == 100:
                self.assertIn("Active repair", output.getvalue())

    def test_manual_historical_action_is_promoted_to_attention(self) -> None:
        state = representative_state()
        state.coverage["rows"].append(
            {
                "coverage_kind": "historical_flatfile_events",
                "status": "manual_action_required",
                "command": "python pipelines/market_sip/flatfiles/download_update_events.py --start 2026-07-10 --end 2026-07-10",
            }
        )
        rows = qmd.attention_rows(state)
        self.assertTrue(any("Run on workstation" in str(row.get("action")) for row in rows))

    def test_missing_required_recent_sessions_are_not_reported_all_clear(self) -> None:
        rows = qmd.attention_rows(representative_state())
        self.assertTrue(any(row.get("area") == "Recent live coverage" for row in rows))


if __name__ == "__main__":
    unittest.main()

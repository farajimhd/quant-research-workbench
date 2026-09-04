from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.reference_gateway.daemon import DaemonCycle, run_reference_daemon


class ReferenceGatewayDaemonTests(unittest.TestCase):
    @patch("services.reference_gateway.daemon.start_reference_api_server")
    @patch("services.reference_gateway.daemon.new_runtime_log_path", return_value="D:/TradingML/runtimes/reference_gateway/test.jsonl")
    @patch("services.reference_gateway.daemon.RuntimeLogger")
    @patch("services.reference_gateway.daemon.run_daemon_cycle")
    @patch("services.reference_gateway.daemon.sleep_for_next_cycle", side_effect=KeyboardInterrupt)
    def test_failed_child_cycle_remains_fail_closed_but_schedules_retry(
        self,
        sleep: Mock,
        run_cycle: Mock,
        logger_type: Mock,
        _log_path: Mock,
        _api: Mock,
    ) -> None:
        config = SimpleNamespace(
            prepared_root_win="D:/TradingML/runtimes/reference_gateway",
            execute=True,
            clickhouse_read_database="q_live",
            clickhouse_write_database="q_live",
            terminal_rich_enabled=False,
            preflight_enabled=False,
        )
        run_cycle.return_value = DaemonCycle(
            active_window=False,
            interval_seconds=3_600.0,
            returncode=2,
            elapsed_seconds=1.0,
            command=["python", "-m", "services.reference_gateway.main"],
            started_at_utc="2026-09-03T00:00:00+00:00",
        )

        with self.assertRaisesRegex(SystemExit, "130"):
            run_reference_daemon(config, [])

        sleep.assert_called_once()
        logger_type.return_value.event.assert_any_call(
            "daemon_cycle_retry_scheduled",
            reason="child_cycle_failed",
            returncode=2,
            retry_seconds=3_600.0,
        )


if __name__ == "__main__":
    unittest.main()

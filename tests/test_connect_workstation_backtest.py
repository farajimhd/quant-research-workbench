from __future__ import annotations

import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scripts import connect_workstation_backtest as connection


class WorkstationBacktestConnectionTests(unittest.TestCase):
    def test_missing_dedicated_key_fails_before_ssh(self) -> None:
        with patch.object(Path, "is_file", return_value=False), patch.object(
                connection.subprocess, "Popen") as launch, patch(
                "sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(connection.main(["--check-only"]), 1)
        launch.assert_not_called()
        self.assertIn("Dedicated workstation SSH key", stderr.getvalue())

    def test_check_only_uses_loopback_and_closes_owned_tunnel(self) -> None:
        process = Mock()
        process.poll.side_effect = [None, None]
        with patch.object(Path, "is_file", return_value=True), patch.object(
                connection.subprocess, "Popen", return_value=process) as launch, patch.object(
                connection, "_api_ready", return_value=True), patch.object(
                connection, "_stop_tunnel") as stop, patch(
                "sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(connection.main(["--check-only", "--local-port", "18000"]), 0)
        command = launch.call_args.args[0]
        self.assertIn("127.0.0.1:18000:127.0.0.1:8000", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        stop.assert_called_once_with(process)
        self.assertIn("connected", stdout.getvalue())

    def test_unresponsive_remote_api_fails_and_closes_tunnel(self) -> None:
        process = Mock()
        process.poll.return_value = None
        with patch.object(Path, "is_file", return_value=True), patch.object(
                connection.subprocess, "Popen", return_value=process), patch.object(
                connection, "_api_ready", return_value=False), patch.object(
                connection, "monotonic", side_effect=[0.0, 16.0]), patch.object(
                connection, "_stop_tunnel") as stop, patch(
                "sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(connection.main(["--check-only"]), 1)
        stop.assert_called_once_with(process)
        self.assertIn("Start scripts/run_backend.py", stderr.getvalue())

    def test_interrupt_closes_only_the_owned_tunnel(self) -> None:
        process = Mock()
        process.poll.return_value = None
        with patch.object(Path, "is_file", return_value=True), patch.object(
                connection.subprocess, "Popen", return_value=process), patch.object(
                connection, "_api_ready", return_value=True), patch.object(
                connection, "sleep", side_effect=KeyboardInterrupt), patch.object(
                connection, "_stop_tunnel") as stop, patch(
                "sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(connection.main([]), 0)
        stop.assert_called_once_with(process)
        self.assertIn("connection closed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

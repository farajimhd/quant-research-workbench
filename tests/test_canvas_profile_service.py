from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.backend.canvas_profile_service import (
    CanvasProfileConflictError,
    editable_canvas_profile,
    save_editable_canvas_profile,
)
from src.trading_runtime.journal import TradingJournal


class CanvasProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.journal = TradingJournal(Path(self.temporary.name) / "journal.sqlite3")
        self.patch = patch(
            "src.backend.canvas_profile_service.trading_journal",
            return_value=self.journal,
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.journal.close()
        self.temporary.cleanup()

    def test_profile_is_persisted_with_optimistic_revision(self) -> None:
        profile = _profile()
        first = save_editable_canvas_profile(profile, expected_revision=0)
        restored = editable_canvas_profile()

        self.assertEqual(first["revision"], 1)
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["profile"], profile)
        self.assertEqual(
            save_editable_canvas_profile(profile, expected_revision=1)["revision"],
            1,
        )

    def test_stale_writer_is_rejected(self) -> None:
        save_editable_canvas_profile(_profile(), expected_revision=0)
        with self.assertRaises(CanvasProfileConflictError):
            save_editable_canvas_profile(_profile("Second"), expected_revision=0)

    def test_unknown_workspace_canvas_is_rejected(self) -> None:
        profile = _profile()
        profile["workspaceStates"]["missing"] = profile["workspaceStates"]["main"]
        with self.assertRaisesRegex(ValueError, "unknown Canvas ids"):
            save_editable_canvas_profile(profile)


def _profile(label: str = "Main") -> dict[str, object]:
    state = {
        "groups": {},
        "instances": {"signal_stream": "signal_stream"},
        "layoutVersion": 1,
        "layouts": {
            "signal_stream": {
                "fullscreen": False,
                "h": 330,
                "minimized": False,
                "w": 800,
                "x": 0,
                "y": 0,
                "z": 1,
            }
        },
        "openIds": ["signal_stream"],
    }
    return {
        "canvases": [{"id": "main", "label": label}],
        "instanceSettings": {},
        "linkAssignments": {},
        "linkContexts": {
            key: {"symbol": "AAPL"} for key in ("A", "B", "C", "D", "E", "F", "G")
        },
        "linkOwners": {},
        "workspaceStates": {"main": state},
        "version": 3,
    }


if __name__ == "__main__":
    unittest.main()

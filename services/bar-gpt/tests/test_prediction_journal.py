from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bar_gpt_service.prediction_journal import PredictionJournal


class PredictionJournalTests(unittest.TestCase):
    def test_visibility_rotation_concurrency_and_restart(self):
        with tempfile.TemporaryDirectory(dir=r"D:\TradingML\runtimes") as tmp:
            root = Path(tmp)
            journal = PredictionJournal(root)
            with patch("bar_gpt_service.prediction_journal.datetime") as clock:
                clock.now.return_value = datetime(2026, 8, 3, tzinfo=UTC)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(journal.append, ({"id": i} for i in range(100))))
                rows = [json.loads(line) for line in (root / "2026-08-03.jsonl").read_text().splitlines()]
                self.assertEqual(sorted(row["id"] for row in rows), list(range(100)))
                old_handle = journal._handle
                clock.now.return_value = datetime(2026, 8, 4, tzinfo=UTC)
                journal.append({"id": 100})
                self.assertTrue(old_handle.closed)
                journal.close()
                journal.close()
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    journal.append({"id": 101})
                replacement = PredictionJournal(root)
                replacement.append({"id": 101})
                replacement.close()
                self.assertEqual([json.loads(line)["id"] for line in
                                  (root / "2026-08-04.jsonl").read_text().splitlines()], [100, 101])

    def test_write_failure_is_not_silenced(self):
        with tempfile.TemporaryDirectory(dir=r"D:\TradingML\runtimes") as tmp:
            journal = PredictionJournal(Path(tmp))
            journal.append({"id": 0})
            with patch.object(journal._handle, "flush", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    journal.append({"id": 1})
            journal.close()


if __name__ == "__main__":
    unittest.main()

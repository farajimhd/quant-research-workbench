from __future__ import annotations

import unittest
from pathlib import Path

from pipelines.market_sip.flatfiles import backfill_execution_clock


class HistoricalSipAuthorityTests(unittest.TestCase):
    def test_execution_clock_repair_cannot_reopen_flatfiles(self) -> None:
        with self.assertRaisesRegex(SystemExit, "may not read retained SIP flatfiles"):
            backfill_execution_clock.main()

        source = Path(backfill_execution_clock.__file__).read_text(encoding="utf-8")
        self.assertNotIn("FROM file(", source)
        self.assertNotIn("source_files(", source)
        self.assertNotIn("rebuild_execution_clock_day", source)


if __name__ == "__main__":
    unittest.main()

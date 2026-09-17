"""Opt-in real-QMD persistence acceptance; never submits labels to the live database."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.request import urlopen
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.backend import chart_labeler_service as service
from src.runtime_paths import project_runtime_root


@unittest.skipUnless(os.environ.get("LABELER_ACCEPTANCE_API"), "Set LABELER_ACCEPTANCE_API to a running backend")
class LabelerLiveAcceptance(unittest.TestCase):
    def test_real_100ms_candles_submit_one_at_a_time_and_reopen(self):
        url = os.environ["LABELER_ACCEPTANCE_API"].rstrip("/")
        with urlopen(url + "/api/research/labeler/chart?session_date=2026-08-21&ticker=SUGP&timeframe=100ms&window=0", timeout=210) as response:
            evidence = json.load(response)
        self.assertGreaterEqual(len(evidence["candles"]), 2)
        scope = evidence["scope"]
        root = project_runtime_root() / "test-chart-labeler"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary, patch.object(service, "project_runtime_root", return_value=Path(temporary)):
            with service.database() as db:
                db.execute("INSERT INTO evidence VALUES (?, ?)", (evidence["evidence_id"], service.canonical(evidence)))
            app = FastAPI()
            app.include_router(service.router)
            ranges = []
            for index, (candle, direction) in enumerate(zip(evidence["candles"][:2], ("LONG", "SHORT"))):
                ranges.append(dict(id=f"real-{index}", direction=direction, annotation_timeframe="100ms",
                    entry_timestamp=service.iso(candle["start_ms"]), exit_timestamp=service.iso(candle["end_ms"]),
                    entry_price=candle["open"], exit_price=candle["close"]))
                # A new client represents reopening the editor after each submission.
                with TestClient(app) as client:
                    previous = client.get("/api/research/labeler/review", params=scope).json()
                    self.assertEqual(previous["ranges"], ranges[:-1])
                    saved = client.put("/api/research/labeler/review", json=dict(scope=scope,
                        expected_revision=previous["revision"], status="in_progress", ranges=ranges,
                        evidence_ids=[evidence["evidence_id"]]))
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertEqual(saved.json()["ranges"], ranges)
            with service.database() as db:
                rows = db.execute("SELECT body FROM label_ranges ORDER BY range_id").fetchall()
                self.assertEqual([json.loads(row[0]) for row in rows], ranges)
                self.assertEqual(db.execute("SELECT count(*) FROM revisions").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()

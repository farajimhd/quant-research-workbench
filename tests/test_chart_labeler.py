"""Boundary, review-state and durable API regressions for the manual labeler."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.backend import chart_labeler_service as service
from src.runtime_paths import project_runtime_root


class LabelerTests(unittest.TestCase):
    def setUp(self):
        root = project_runtime_root() / "test-chart-labeler"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.patch = patch.object(service, "project_runtime_root", return_value=Path(self.temp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.scope = service.Scope(session_date=date(2026, 9, 15), ticker="AAA", timeframe="100ms")
        self.start, self.end = service.bounds(self.scope.session_date, "regular")
        self.evidence = {"scope": self.scope.model_dump(mode="json"), "source_token": "source-v1", "instrument_id": "listing-a",
                         "start_ms": self.start, "end_ms": self.end,
                         "candles": [{"start_ms": self.start + 100, "end_ms": self.start + 200, "open": 10.0, "close": 10.5},
                                     {"start_ms": self.start + 200, "end_ms": self.start + 300, "open": 10.5, "close": 10.2}]}
        self.evidence_id = service.digest(self.evidence)
        with service.database() as db:
            db.execute("INSERT INTO evidence VALUES (?, ?)", (self.evidence_id, service.canonical(self.evidence)))
        app = FastAPI()
        app.include_router(service.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def interval(self, **changes):
        return {"id": "a", "direction": "LONG", "annotation_timeframe": "100ms", "entry_timestamp": service.iso(self.start + 100),
                "exit_timestamp": service.iso(self.start + 200), "entry_price": 10.0, "exit_price": 10.5, **changes}

    def request(self, **changes):
        return {"scope": self.scope.model_dump(mode="json"), "expected_revision": 0, "status": "completed",
                "ranges": [self.interval()], "evidence_ids": [self.evidence_id], **changes}

    def put(self, **changes):
        return self.client.put("/api/research/labeler/review", json=self.request(**changes))

    def test_single_candle_keeps_two_exact_boundaries_and_survives_reload(self):
        saved = self.put()
        self.assertEqual(saved.status_code, 200, saved.text)
        loaded = self.client.get("/api/research/labeler/review", params=self.scope.model_dump(mode="json")).json()
        self.assertEqual(loaded["ranges"][0]["entry_timestamp"], service.iso(self.start + 100))
        exported = self.client.get("/api/research/labeler/export").json()["reviews"][0]
        self.assertEqual([e["event"] for e in exported["events"]], ["ENTER_LONG", "EXIT_LONG"])
        self.assertEqual(service.millis(exported["events"][1]["timestamp"]) - service.millis(exported["events"][0]["timestamp"]), 100)

    def test_each_submission_is_durable_before_session_completion(self):
        first = self.interval()
        second = self.interval(id="b", direction="SHORT", entry_timestamp=service.iso(self.start + 200), exit_timestamp=service.iso(self.start + 300), entry_price=10.5, exit_price=10.2)
        self.assertEqual(self.put(status="in_progress", ranges=[first]).status_code, 200)
        with service.database() as db:
            self.assertEqual([json.loads(row[0]) for row in db.execute("SELECT body FROM label_ranges")], [first])
        loaded = self.client.get("/api/research/labeler/review", params=self.scope.model_dump(mode="json")).json()
        self.assertEqual(loaded["ranges"], [first])
        self.assertEqual(self.put(status="in_progress", ranges=[first, second], expected_revision=loaded["revision"]).status_code, 200)
        # A stale editor cannot erase either submitted position.
        self.assertEqual(self.put(status="in_progress", ranges=[], expected_revision=0).status_code, 409)
        with service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM label_ranges").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM revisions").fetchone()[0], 2)
        self.assertEqual(self.client.get("/api/research/labeler/export").json()["reviews"], [])
        self.assertEqual(self.put(status="completed", ranges=[first, second], expected_revision=2).status_code, 200)
        self.assertEqual(len(self.client.get("/api/research/labeler/export").json()["reviews"][0]["ranges"]), 2)

    def test_existing_review_ranges_are_migrated_without_timestamp_changes(self):
        self.assertEqual(self.put().status_code, 200)
        with service.database() as db:
            db.execute("DELETE FROM label_ranges")
            db.execute("PRAGMA user_version=0")
        with service.database() as db:
            self.assertEqual(json.loads(db.execute("SELECT body FROM label_ranges").fetchone()[0]), self.interval())
            self.assertEqual(db.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)

    def test_invalid_review_query_is_validation_error(self):
        response = self.client.get("/api/research/labeler/review", params={**self.scope.model_dump(mode="json"), "timeframe": "bogus"})
        self.assertEqual(response.status_code, 422)

    def test_retries_idempotent_and_conflicting_editor_rejected(self):
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(self.put().json()["revision"], 1)
        self.assertEqual(self.put(ranges=[self.interval(direction="SHORT")]).status_code, 409)
        with service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)

    def test_short_and_adjacent_long_preserve_exit_then_entry(self):
        second = self.interval(id="b", direction="SHORT", entry_timestamp=service.iso(self.start + 200), exit_timestamp=service.iso(self.start + 300), entry_price=10.5, exit_price=10.2)
        self.assertEqual(self.put(ranges=[self.interval(), second]).status_code, 200)
        events = self.client.get("/api/research/labeler/export").json()["reviews"][0]["events"]
        self.assertEqual([e["event"] for e in events], ["ENTER_LONG", "EXIT_LONG", "ENTER_SHORT", "EXIT_SHORT"])
        self.assertEqual(events[1]["timestamp"], events[2]["timestamp"])

    def test_overlap_and_price_fabrication_rejected(self):
        self.assertEqual(self.put(ranges=[self.interval(), self.interval(id="b", direction="SHORT")]).status_code, 422)
        self.assertEqual(self.put(ranges=[self.interval(entry_price=9)]).status_code, 422)
        self.assertEqual(self.put(ranges=[self.interval(exit_timestamp=service.iso(self.start + 150))]).status_code, 422)

    def test_no_opportunity_explicit_and_unreviewed_not_negative(self):
        self.assertEqual(self.client.get("/api/research/labeler/export").json()["reviews"], [])
        self.assertEqual(self.put(status="no_opportunity").status_code, 422)
        self.assertEqual(self.put(status="no_opportunity", ranges=[]).status_code, 200)
        exported = self.client.get("/api/research/labeler/export").json()["reviews"][0]
        self.assertEqual(exported["events"], [])
        self.assertEqual(exported["segments"], [{"start": service.iso(self.start), "end": service.iso(self.end), "state": "WAIT"}])

    def test_review_is_session_scoped_not_view_timeframe_scoped(self):
        self.assertEqual(self.put().status_code, 200)
        params = {**self.scope.model_dump(mode="json"), "timeframe": "1h"}
        self.assertEqual(self.client.get("/api/research/labeler/review", params=params).json()["revision"], 1)

    def test_unfinished_not_exported_and_reopen_revokes_completion(self):
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(self.put(expected_revision=1, status="in_progress").status_code, 200)
        exported = self.client.get("/api/research/labeler/export").json()
        self.assertEqual(exported["reviews"], [])
        self.assertEqual(exported["excluded_unfinished_reviews"], 1)

    def test_partial_coverage_cannot_complete(self):
        self.evidence["end_ms"] = self.start + 1800000
        with service.database() as db:
            db.execute("UPDATE evidence SET body=?", (service.canonical(self.evidence),))
        self.assertEqual(self.put().status_code, 422)
        self.assertEqual(self.put(status="in_progress").status_code, 200)

    def test_unknown_or_mixed_evidence_rejected(self):
        self.assertEqual(self.put(evidence_ids=["missing"]).status_code, 422)
        other = {**self.evidence, "source_token": "changed"}
        with service.database() as db:
            db.execute("INSERT INTO evidence VALUES (?, ?)", ("other", service.canonical(other)))
        self.assertEqual(self.put(evidence_ids=[self.evidence_id, "other"]).status_code, 422)

    def test_utc_precision_dst_and_early_close(self):
        self.assertEqual(service.millis("2026-09-15T09:30:00.100-04:00"), self.start + 100)
        with self.assertRaises(ValueError):
            service.millis("2026-09-15T09:30:00.100001-04:00")
        with self.assertRaises(ValueError):
            service.millis("2026-09-15T09:30:00.100")
        a, b = service.bounds(date(2025, 11, 28), "regular")
        self.assertEqual(b - a, 210 * 60000)
        self.assertIn("14:30", service.iso(a))

    @patch("src.backend.historical_scanner_service.historical_scanner_reference_projection")
    @patch("src.backend.qmd_gateway_client.qmd_history_get_json")
    def test_identity_universe_does_not_wait_for_market(self, market, reference):
        reference.return_value = {"AAA": {"listing_id": "a", "float_shares": 1000}}
        result = self.client.get("/api/research/labeler/universe", params={"session_date": "2026-09-15", "include_market": False})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["rows"][0]["ticker"], "AAA")
        market.assert_not_called()

    @patch("src.backend.qmd_gateway_client.qmd_history_get_json")
    def test_market_summary_uses_full_market_authority(self, market):
        market.return_value = {"rows": [{"symbol": "AAA", "volume": 100}]}
        result = self.client.get("/api/research/labeler/market", params={"session_date": "2026-09-15"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn("tickers", market.call_args.args[1])

    @patch("src.backend.historical_scanner_service.historical_scanner_reference_projection")
    @patch("src.backend.qmd_gateway_client.qmd_history_get_json")
    def test_universe_reads_entire_session_and_preserves_market_fields(self, market, reference):
        reference.return_value = {"AAA": {"listing_id": "a", "float_shares": 1000}, "ABR PRD": {"listing_id": "preferred"}}
        market.return_value = {"rows": [{"symbol": "AAA", "last": 11, "change_pct": 10, "volume": 12000}]}
        result = self.client.get("/api/research/labeler/universe", params={"session_date": "2026-09-15"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["rows"][0]["change_pct"], 10)
        self.assertEqual(result.json()["rows"][0]["volume"], 12000)
        self.assertEqual(len(result.json()["rows"]), 2)
        params = market.call_args.args[1]
        self.assertEqual(service.millis(params["end"]) - service.millis(params["start"]), 390 * 60000)

    @patch("src.backend.historical_scanner_service.historical_scanner_reference_projection")
    @patch("src.backend.qmd_gateway_client.qmd_product_request")
    @patch("src.backend.qmd_gateway_client.qmd_historical_source_revision")
    def test_chart_rejects_uncertified_truncated_or_changing_sources(self, revision, product, reference):
        reference.return_value = {"AAA": {"listing_id": "a"}}
        revision.return_value = {"token": "v1"}
        params = self.scope.model_dump(mode="json")
        product.return_value = SimpleNamespace(payload={"bars": [], "has_more": True}, complete=None)
        self.assertEqual(self.client.get("/api/research/labeler/chart", params=params).status_code, 503)
        self.assertEqual(product.call_args.args[0].stage, "prices")
        product.return_value = SimpleNamespace(payload={"bars": []}, complete=None)
        self.assertEqual(self.client.get("/api/research/labeler/chart", params=params).status_code, 503)
        certified = {"request_complete": True, "complete_for_history": True}
        product.return_value = SimpleNamespace(payload={"bars": [], "cache": {"source_revision": certified}}, complete=None)
        revision.side_effect = [{"token": "v1"}, {"token": "v2"}]
        self.assertEqual(self.client.get("/api/research/labeler/chart", params=params).status_code, 503)

    def test_empty_chart_and_wrong_annotation_timeframe_cannot_certify(self):
        self.assertEqual(self.put(ranges=[self.interval(annotation_timeframe="1h")]).status_code, 422)
        self.evidence["candles"] = []
        with service.database() as db:
            db.execute("UPDATE evidence SET body=?", (service.canonical(self.evidence),))
        self.assertEqual(self.put(status="no_opportunity", ranges=[]).status_code, 422)


if __name__ == "__main__":
    unittest.main()

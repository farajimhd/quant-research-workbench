from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.backend.market_data_service import chart_source_revision


class ChartCacheRevisionTests(unittest.TestCase):
    def test_revision_changes_with_artifact_build_identity(self) -> None:
        root = Path("D:/processed")
        record = {
            "key": "bars|1m|2026-08-10",
            "path": "D:/processed/bars.parquet",
            "size_bytes": 12,
            "built_at": "2026-08-10T12:00:00",
            "build_id": "build-1",
            "schema_version": 1,
            "feature_version": 2,
            "supervision_version": 3,
            "timeframe": "1m",
            "session_date": "2026-08-10",
        }
        with patch(
            "src.backend.market_data_service.artifact_records",
            return_value=[record],
        ):
            first = chart_source_revision(
                root, date(2026, 8, 10), date(2026, 8, 10), "1m"
            )
            record["build_id"] = "build-2"
            second = chart_source_revision(
                root, date(2026, 8, 10), date(2026, 8, 10), "1m"
            )

        self.assertNotEqual(first, second)

    def test_revision_changes_with_presentation_override(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "src.backend.market_data_service.artifact_records",
                return_value=[],
            ):
                first = chart_source_revision(
                    root, date(2026, 8, 10), date(2026, 8, 10), "1m"
                )
                override = root / "catalog_presentation_overrides.json"
                override.write_text("{}", encoding="utf-8")
                second = chart_source_revision(
                    root, date(2026, 8, 10), date(2026, 8, 10), "1m"
                )

        self.assertNotEqual(first, second)

    def test_cached_chart_payload_keys_by_source_revision(self) -> None:
        from src.backend import app as backend_app

        backend_app.cached_chart_payload.cache_clear()
        arguments = (
            "D:/processed",
            "2026-08-10",
            "2026-08-10",
            "1m",
            "AAPL",
            (),
            (),
            None,
            (),
            100,
            0.7,
        )
        with patch.object(backend_app, "chart_payload", return_value={"ok": True}) as chart:
            backend_app.cached_chart_payload(arguments[0], "revision-1", *arguments[1:])
            backend_app.cached_chart_payload(arguments[0], "revision-1", *arguments[1:])
            backend_app.cached_chart_payload(arguments[0], "revision-2", *arguments[1:])

        self.assertEqual(chart.call_count, 2)

    def test_market_chart_resolves_revision_before_cache_lookup(self) -> None:
        from src.backend import app as backend_app

        with (
            patch.object(
                backend_app,
                "chart_source_revision",
                return_value="revision-7",
            ) as revision,
            patch.object(
                backend_app,
                "cached_chart_payload",
                return_value={"candles": []},
            ) as cached,
        ):
            payload = backend_app.market_chart(
                "D:/processed",
                "1m",
                "aapl",
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 10),
                columns="close",
            )

        self.assertEqual(payload, {"candles": []})
        revision.assert_called_once_with(
            Path("D:/processed"),
            date(2026, 8, 10),
            date(2026, 8, 10),
            "1m",
        )
        self.assertEqual(cached.call_args.args[1], "revision-7")

    def test_canvas_history_coalesces_identical_initial_pages(self) -> None:
        from src.backend import app as backend_app

        with patch.object(
            backend_app,
            "_canvas_live_chart_history",
            return_value={"history": [{"bar_start": "2026-08-17T13:44:00Z"}]},
        ) as history:
            first = backend_app.trading_canvas_live_chart_history(
                "ZZTEST", timeframe="1m", session_date="2026-08-17",
                as_of="2026-08-17T13:45:00Z", row_limit=20, stage="bars",
            )
            second = backend_app.trading_canvas_live_chart_history(
                "ZZTEST", timeframe="1m", session_date="2026-08-17",
                as_of="2026-08-17T13:45:00Z", row_limit=20, stage="bars",
            )

        self.assertEqual(first, second)
        history.assert_called_once()


if __name__ == "__main__":
    unittest.main()

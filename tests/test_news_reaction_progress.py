from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from rich.console import Console

from pipelines.news.benzinga.news_reaction_progress import NewsReactionProgress


class NewsReactionProgressTests(unittest.TestCase):
    def make_reporter(self, layout: str = "text") -> NewsReactionProgress:
        return NewsReactionProgress(
            stage_totals=(("preflight", 2), ("features", 3), ("reactions", 5), ("audit", 1)),
            run_id="test-run",
            run_root="D:/tmp/test-run",
            layout=layout,
            refresh_per_second=2.0,
            log_lines=5,
        )

    def test_text_progress_reports_active_and_durable_lifecycle(self) -> None:
        output = io.StringIO()
        reporter = self.make_reporter()
        with contextlib.redirect_stdout(output), reporter:
            reporter.stage_start("features")
            reporter.chunk_start("features", "2025-01-01:2025-02-01")
            reporter.query_start("feature insert", "query-1")
            reporter.query_done("feature insert")
            reporter.unit_done("features", "2025-01-01:2025-02-01", status="complete", rows=42, elapsed_seconds=1.25)
            reporter.finish()
        text = output.getvalue()
        self.assertIn("unit start stage=features", text)
        self.assertIn("query start feature insert query_id=query-1", text)
        self.assertIn("rows=42", text)
        self.assertIn("progress=1/3", text)
        self.assertIn("news reaction build complete", text)

    def test_progress_advances_only_after_unit_done(self) -> None:
        reporter = self.make_reporter()
        with contextlib.redirect_stdout(io.StringIO()):
            reporter.stage_start("reactions")
            reporter.chunk_start("reactions", "2025-01-02:2025-01-03")
            self.assertEqual(reporter.stages["reactions"].processed, 0)
            reporter.unit_done("reactions", "2025-01-02:2025-01-03", status="skipped")
        self.assertEqual(reporter.stages["reactions"].processed, 1)
        self.assertEqual(reporter.stages["reactions"].skipped, 1)

    def test_normal_and_compact_rich_render_fit_without_error(self) -> None:
        reporter = self.make_reporter()
        with contextlib.redirect_stdout(io.StringIO()):
            reporter.stage_start("reactions")
            reporter.chunk_start("reactions", "2025-01-02:2025-01-03")
            reporter.query_start("reaction insert", "query-2")
        for terminal_size in ((120, 40), (72, 20)):
            with self.subTest(terminal_size=terminal_size), mock.patch(
                "pipelines.news.benzinga.news_reaction_progress.shutil.get_terminal_size",
                return_value=terminal_size,
            ):
                output = io.StringIO()
                console = Console(file=output, width=terminal_size[0], height=terminal_size[1], force_terminal=True, color_system=None)
                console.print(reporter._render())
                rendered = output.getvalue()
                self.assertIn("News Reaction Labels", rendered)
                self.assertIn("reaction insert", rendered)
                self.assertLessEqual(max(len(line) for line in rendered.splitlines()), terminal_size[0])

    def test_failure_remains_visible(self) -> None:
        reporter = self.make_reporter()
        with contextlib.redirect_stdout(io.StringIO()):
            reporter.unit_failed("reactions", "2025-01-02:2025-01-03", RuntimeError("ClickHouse unavailable"))
        self.assertEqual(reporter.outcome, "failed")
        self.assertIn("ClickHouse unavailable", reporter.last_error)
        self.assertEqual(reporter.stages["reactions"].failed, 1)


if __name__ == "__main__":
    unittest.main()

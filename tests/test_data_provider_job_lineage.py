from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.data_provider.config import BuildRequest
from src.data_provider.jobs import (
    BUILD_CAUSATION_ENV,
    BUILD_CORRELATION_ENV,
    append_event,
    events_file,
    read_job,
    request_to_dict,
    resume_build_job,
    start_build_worker,
    submit_build_job,
    write_job,
)
from src.request_context import begin_request_context, end_request_context


class DataProviderJobLineageTests(unittest.TestCase):
    def test_submitted_job_persists_request_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = BuildRequest(
                raw_root=Path(temporary) / "raw",
                spread_root=Path(temporary) / "spread",
                processed_root=Path(temporary) / "processed",
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 10),
            )
            tokens = begin_request_context("web:build-7", "command:submit")
            try:
                with patch("src.data_provider.jobs.start_build_worker", side_effect=lambda _path, payload, **_kwargs: payload):
                    payload = submit_build_job(request)
            finally:
                end_request_context(tokens[0], tokens[1])
            persisted = read_job(request.processed_root / "jobs" / payload["job_id"])
        self.assertEqual(persisted["lineage"]["correlation_id"], "web:build-7")
        self.assertEqual(persisted["lineage"]["causation_id"], "command:submit")
        self.assertIsNone(persisted["lineage"]["parent_build_id"])

    def test_autonomous_events_keep_job_root_and_get_stable_event_causation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job-17"
            write_job(
                path,
                {
                    "job_id": "job-17",
                    "lineage": {
                        "correlation_id": "run:job-17",
                        "causation_id": "command:submit-17",
                    },
                },
            )
            event = {"event": "artifact_complete", "phase": "write", "session_date": "2026-08-10"}
            append_event(path, event)
            append_event(path, event)
            rows = [json.loads(line) for line in events_file(path).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["correlation_id"], "run:job-17")
        self.assertEqual(rows[0]["parent_causation_id"], "command:submit-17")
        self.assertEqual(rows[0]["causation_id"], rows[1]["causation_id"])
        self.assertTrue(rows[0]["causation_id"].startswith("event:"))

    def test_stateful_retry_keeps_original_correlation_and_cites_resume_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            processed_root = Path(temporary) / "processed"
            source_request = BuildRequest(
                raw_root=Path(temporary) / "raw",
                spread_root=Path(temporary) / "spread",
                processed_root=processed_root,
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 10),
            )
            source_path = processed_root / "jobs" / "source-job"
            write_job(
                source_path,
                {
                    "job_id": "source-job",
                    "build_name": "source-build",
                    "status": "failed",
                    "request": request_to_dict(source_request),
                    "resources": {"session_workers": 2, "polars_threads": 3},
                    "lineage": {
                        "correlation_id": "run:original-build",
                        "causation_id": "event:original-failure",
                    },
                },
            )
            tokens = begin_request_context("web:retry-9", "command:retry-stateful")
            try:
                with patch("src.data_provider.jobs.start_build_worker", side_effect=lambda _path, payload, **_kwargs: payload):
                    resumed = resume_build_job(processed_root, "source-job")
            finally:
                end_request_context(tokens[0], tokens[1])
        self.assertEqual(resumed["lineage"]["correlation_id"], "run:original-build")
        self.assertEqual(resumed["lineage"]["causation_id"], "command:retry-stateful")
        self.assertEqual(resumed["lineage"]["parent_build_id"], "source-job")
        self.assertEqual(resumed["lineage"]["parent_causation_id"], "event:original-failure")

    @patch("src.data_provider.jobs.subprocess.Popen")
    def test_worker_receives_durable_job_lineage(self, popen) -> None:
        popen.return_value = MagicMock(pid=317)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job-31"
            payload = {
                "job_id": "job-31",
                "status": "queued",
                "lineage": {
                    "correlation_id": "run:job-31",
                    "causation_id": "event:source-31",
                },
            }
            write_job(path, payload)
            start_build_worker(path, payload, polars_threads=3)
            environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment[BUILD_CORRELATION_ENV], "run:job-31")
        self.assertEqual(environment[BUILD_CAUSATION_ENV], "event:source-31")


if __name__ == "__main__":
    unittest.main()

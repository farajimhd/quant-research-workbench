from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.request_context import (
    CAUSATION_HEADER,
    ContextThreadPoolExecutor,
    CORRELATION_HEADER,
    begin_request_context,
    current_request_headers,
    current_request_identity,
    current_request_query,
    end_request_context,
    normalize_request_identity,
    causal_identity,
    stable_causal_identity,
)
from src.trading_runtime.domain import (
    BrokerEventEnvelope,
    BrokerEventType,
    BrokerProvider,
    TradingMode,
)
from src.trading_runtime.journal import TradingJournal


class RequestContextTests(unittest.TestCase):
    def test_context_preserves_valid_identity_and_defaults_causation(self) -> None:
        correlation_token, causation_token, correlation, causation = begin_request_context(
            "web:request-17",
            None,
        )
        try:
            self.assertEqual(correlation, "web:request-17")
            self.assertEqual(causation, correlation)
            self.assertEqual(
                current_request_headers(),
                {
                    CORRELATION_HEADER: correlation,
                    CAUSATION_HEADER: correlation,
                },
            )
            self.assertEqual(current_request_query()["correlation_id"], correlation)
            self.assertEqual(current_request_identity()["causation_id"], correlation)
        finally:
            end_request_context(correlation_token, causation_token)
        self.assertEqual(current_request_headers(), {})

    def test_invalid_or_oversized_identity_is_rejected(self) -> None:
        self.assertEqual(normalize_request_identity("unsafe value"), "")
        self.assertEqual(normalize_request_identity("a" * 129), "")
        self.assertEqual(normalize_request_identity("safe.id_1:child"), "safe.id_1:child")

    def test_autonomous_identity_is_bounded_and_stable(self) -> None:
        first = causal_identity(
            correlation_seed="assignment-7",
            causation_seed="AAPL:2026-08-10T14:30:00+00:00",
        )
        second = causal_identity(
            correlation_seed="assignment-7",
            causation_seed="AAPL:2026-08-10T14:30:00+00:00",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["correlation_id"], "run:assignment-7")
        self.assertTrue(first["causation_id"].startswith("event:"))
        self.assertLessEqual(len(first["causation_id"]), 128)
        self.assertEqual(
            stable_causal_identity("event", "source-1"),
            "event:source-1",
        )

    def test_context_flows_into_broker_events_and_authoritative_journal_records(self) -> None:
        correlation_token, causation_token, _, _ = begin_request_context(
            "web:request-19", "proposal:chart-7"
        )
        try:
            event = BrokerEventEnvelope.create(
                event_type=BrokerEventType.ORDER_COMMAND,
                provider=BrokerProvider.SIMULATED,
                mode=TradingMode.REPLAY,
                account_id="SIM",
                payload={"command": "submit"},
            )
            with tempfile.TemporaryDirectory() as temporary:
                journal = TradingJournal(Path(temporary) / "journal.sqlite3")
                try:
                    record = journal.append(
                        run_id="run-1",
                        category="portfolio_management",
                        entity_type="portfolio_decision",
                        entity_id="decision-1",
                        payload={"status": "approved"},
                    )
                finally:
                    journal.close()
        finally:
            end_request_context(correlation_token, causation_token)

        self.assertEqual(event.correlation_id, "web:request-19")
        self.assertEqual(event.causation_id, "proposal:chart-7")
        self.assertEqual(record.payload["correlation_id"], "web:request-19")
        self.assertEqual(record.payload["causation_id"], "proposal:chart-7")

    def test_context_flows_into_concurrent_backend_composition(self) -> None:
        correlation_token, causation_token, _, _ = begin_request_context(
            "web:request-27", "chart:AAPL"
        )
        try:
            with ContextThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(current_request_identity)
                second = executor.submit(current_request_headers)
                identity = first.result(timeout=2)
                headers = second.result(timeout=2)
        finally:
            end_request_context(correlation_token, causation_token)
        self.assertEqual(identity["correlation_id"], "web:request-27")
        self.assertEqual(identity["causation_id"], "chart:AAPL")
        self.assertEqual(headers[CORRELATION_HEADER], "web:request-27")
        self.assertEqual(headers[CAUSATION_HEADER], "chart:AAPL")

    def test_concurrent_backend_context_does_not_leak_between_submissions(self) -> None:
        with ContextThreadPoolExecutor(max_workers=1) as executor:
            first_tokens = begin_request_context("web:first", "event:first")
            try:
                first = executor.submit(current_request_identity)
            finally:
                end_request_context(first_tokens[0], first_tokens[1])
            second_tokens = begin_request_context("web:second", "event:second")
            try:
                second = executor.submit(current_request_identity)
            finally:
                end_request_context(second_tokens[0], second_tokens[1])
            self.assertEqual(first.result(timeout=2)["correlation_id"], "web:first")
            self.assertEqual(second.result(timeout=2)["correlation_id"], "web:second")


if __name__ == "__main__":
    unittest.main()

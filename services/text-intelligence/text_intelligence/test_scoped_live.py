from __future__ import annotations

import unittest
from unittest import mock

from .scoped_live import ScopedTextRuntime, TextDocumentNotice


class ScopedTextRuntimeTests(unittest.TestCase):
    def test_sec_reload_passes_physical_document_partition(self) -> None:
        class Client:
            def iter_json_each_row(self, _sql):
                return iter(
                    (
                        {
                            "filing_id": "filing-1",
                            "cik": "0000000001",
                            "accession_number": "0000000001-26-000001",
                            "document_partition": 37,
                            "source_timestamp": "2026-07-29 12:00:00",
                            "company_name": "Example Corp",
                            "form_type": "8-K",
                            "filing_items": "8.01",
                            "filing_date": "2026-07-29",
                            "report_date": "2026-07-29",
                            "accepted_at_source": "20260729120000",
                        },
                    )
                )

        runtime = ScopedTextRuntime(
            client=Client(),
            database="q_live",
            live_news=mock.Mock(),
        )
        with (
            mock.patch(
                "text_intelligence.scoped_live.extend_sec_ticker_mappings"
            ),
            mock.patch(
                "text_intelligence.scoped_live."
                "iter_sec_documents_for_filings",
                return_value=iter(()),
            ) as documents,
        ):
            loaded = runtime._load_sec(
                TextDocumentNotice(
                    corpus="sec",
                    source_id="0000000001-26-000001",
                )
            )

        self.assertEqual(loaded.rows, ())
        self.assertEqual(documents.call_args.kwargs["partition"], 37)


if __name__ == "__main__":
    unittest.main()

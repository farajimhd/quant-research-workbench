"""Bounded request-only source reuse for immutable configuration exploration."""
from __future__ import annotations

from contextlib import closing

from .backtest_market_data import assert_select_only, readonly_clickhouse_client


class ConfigurationOptionReader:
    """Reuse exact SELECT text, then re-read every source before returning.

    The certified reader still validates every ancestor and seal. No evidence
    survives the request, and a concurrent source change rejects the response.
    """

    def __init__(self, client, *, max_queries=256, max_bytes=64 * 1024 * 1024):
        self.client = client
        self.max_queries = max_queries
        self.max_bytes = max_bytes
        self._responses = {}
        self._bytes = 0

    def execute(self, query):
        assert_select_only(query)
        if query in self._responses:
            return self._responses[query]
        if len(self._responses) >= self.max_queries:
            raise ValueError("Configuration options exceed the bounded source-query budget")
        result = self.client.execute(query)
        if not isinstance(result, str):
            raise ValueError("Configuration options require immutable text source responses")
        size = len(query.encode("utf-8")) + len(result.encode("utf-8"))
        if self._bytes + size > self.max_bytes:
            raise ValueError("Configuration options exceed the bounded source-memory budget")
        self._responses[query] = result
        self._bytes += size
        return result

    def verify_unchanged(self):
        for query, expected in self._responses.items():
            if self.client.execute(query) != expected:
                raise RuntimeError("Configuration options source changed during certification")


def certified_configuration_options(candidate_id=""):
    from .backtest_strategy_one_configuration import (
        selected_numbered_revision, numbered_configuration_options,
    )

    with closing(readonly_clickhouse_client(v3_read_principal=True)) as client:
        reader = ConfigurationOptionReader(client)
        revision = selected_numbered_revision(revision_id=candidate_id, client=reader)
        options = numbered_configuration_options(reader)
        reader.verify_unchanged()
        return revision, options

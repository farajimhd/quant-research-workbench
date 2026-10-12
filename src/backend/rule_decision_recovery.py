"""Owned asynchronous reads of staged shared decision references.

This reader does not admit a strategy or prove its referenced market/financial
state. Publication and independent causal replay remain required.
"""
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.rule_decision_basis_projection import load_committed_rule_decision_page


async def read_rule_decision_page_async(reader_factory, prefix, *, after_sequence=0, limit=200):
    """Keep database work off-loop and drain the owned client on cancellation."""
    if (not callable(reader_factory) or type(prefix) is not V4CommittedPrefix
            or not prefix.batch_ids or type(after_sequence) is not int
            or not 0 <= after_sequence <= prefix.last_sequence
            or type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError("Rule decision recovery requires a bounded exact V4 prefix")

    def read():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != "1":
                raise ValueError("Rule decision recovery requires a SELECT-only client")
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Rule decision prefix changed before readback")
            rows = load_committed_rule_decision_page(client, prefix,
                after_sequence=after_sequence, limit=limit)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Rule decision prefix changed after readback")
            return rows
        finally:
            client.close()

    return await _owned_worker(read)

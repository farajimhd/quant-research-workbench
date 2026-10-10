"""Recompute normalized pending channel evidence; no checkpoint/order admission."""
import polars as pl

from .backtest_native_channel_exit_request import build_native_channel_exit_request
from .backtest_native_channel_exit_projection import project_native_channel_exit
from src.trading_runtime.native_acquisition_clock import acquisition_clock_from_entry_executions
from src.trading_runtime.arte_native_channel_exit_schema import DECISION, SOURCE_ROW, EXECUTION_ROW
from src.trading_runtime.arte_journal_writer import verify_typed_contract_row


def reprove_native_channel_exit_rows(header, sources, fills, *, prepared, policy, quotes,
        group, executions, original_entry, source_entry_intent_id, current_financial,
        checkpoint, session_date, stored_utc=False, **lineage_context):
    """Reject self-consistent saved rows that differ from recomputed native facts.

    The owner supplies independently certified preparation, raw OMS/broker
    fills, independently sourced quotes, original entry and current financial
    state. Snapshot roots and committed batch membership still need independent
    journal verification.
    This helper performs no transport read, publication or order submission.
    """
    limit = lineage_context['max_rows_per_family']
    if (type(limit) is not int or limit < 1 or type(sources) is not tuple
            or type(fills) is not tuple or len(sources) > limit or len(fills) > limit):
        raise ValueError('Exact bounded normalized source and fill families required')
    saved_header = verify_typed_contract_row(DECISION, header, stored_utc=stored_utc)
    saved_sources = tuple(verify_typed_contract_row(SOURCE_ROW, row, stored_utc=stored_utc) for row in sources)
    saved_fills = tuple(verify_typed_contract_row(EXECUTION_ROW, row, stored_utc=stored_utc) for row in fills)
    if (saved_header['source_row_count'] != len(saved_sources)
            or saved_header['execution_row_count'] != len(saved_fills)):
        raise ValueError('Normalized channel family counts differ from saved decision')
    clock = acquisition_clock_from_entry_executions(group, executions,
        session_date=session_date, as_of=lineage_context['as_of'],
        source_entry_intent_id=source_entry_intent_id, expected_conid=lineage_context['expected_conid'])
    decisions = pl.DataFrame(dict(build_id=[saved_header['source_build_id']],
        session_date=[saved_header['session_date']], ticker=[saved_header['ticker']],
        attempt_id=[saved_header['source_bars_attempt_id']], decision_day_ms=[saved_header['decision_day_ms']]),
        schema=dict(build_id=pl.String, session_date=pl.String, ticker=pl.String,
                    attempt_id=pl.String, decision_day_ms=pl.Int64))
    request = build_native_channel_exit_request(prepared, decisions, quotes, policy=policy,
        financial=current_financial, acquisition=clock, source_entry_intent_id=source_entry_intent_id)
    if request is None:
        raise ValueError('Saved channel exit is no longer reproduced by certified source and native ownership')
    expected = project_native_channel_exit(request, group, executions,
        original_entry=original_entry, source_entry_intent_id=source_entry_intent_id,
        current_financial=current_financial, checkpoint=checkpoint, **lineage_context)
    for contract, actual, computed in ((DECISION, (saved_header,), (expected[0],)),
            (SOURCE_ROW, saved_sources, expected[1]), (EXECUTION_ROW, saved_fills, expected[2])):
        expected_rows = tuple(verify_typed_contract_row(contract, row) for row in computed)
        order = lambda row: row['record_id']
        if sorted(actual, key=order) != sorted(expected_rows, key=order):
            raise ValueError('Saved normalized channel evidence differs from independent recomputation')
    return request

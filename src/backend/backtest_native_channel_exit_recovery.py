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
    return _reprove_native_channel_rows(header, sources, fills,
        contracts=(DECISION, SOURCE_ROW, EXECUTION_ROW), projector=project_native_channel_exit,
        projector_context=dict(checkpoint=checkpoint), prepared=prepared, policy=policy, quotes=quotes,
        group=group, executions=executions, original_entry=original_entry,
        source_entry_intent_id=source_entry_intent_id, current_financial=current_financial,
        session_date=session_date, stored_utc=stored_utc, **lineage_context)


def _reprove_native_channel_rows(header, sources, fills, *, contracts, projector,
        projector_context, prepared, policy, quotes, group, executions, original_entry,
        source_entry_intent_id, current_financial, session_date, stored_utc, **lineage_context):
    header_contract, source_contract, execution_contract = contracts
    limit = lineage_context['max_rows_per_family']
    if (type(limit) is not int or limit < 1 or type(sources) is not tuple
            or type(fills) is not tuple or len(sources) > limit or len(fills) > limit):
        raise ValueError('Exact bounded normalized source and fill families required')
    saved_header = verify_typed_contract_row(header_contract, header, stored_utc=stored_utc)
    saved_sources = tuple(verify_typed_contract_row(source_contract, row, stored_utc=stored_utc) for row in sources)
    saved_fills = tuple(verify_typed_contract_row(execution_contract, row, stored_utc=stored_utc) for row in fills)
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
    expected = projector(request, group, executions,
        original_entry=original_entry, source_entry_intent_id=source_entry_intent_id,
        current_financial=current_financial, **projector_context, **lineage_context)
    for contract, actual, computed in ((header_contract, (saved_header,), (expected[0],)),
            (source_contract, saved_sources, expected[1]), (execution_contract, saved_fills, expected[2])):
        expected_rows = tuple(verify_typed_contract_row(contract, row) for row in computed)
        order = lambda row: row['record_id']
        if sorted(actual, key=order) != sorted(expected_rows, key=order):
            raise ValueError('Saved normalized channel evidence differs from independent recomputation')
    return request

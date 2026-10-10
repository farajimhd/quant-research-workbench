from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_request import inputs, INTENT
from tests.test_backtest_native_channel_exit_projection import context
from tests.test_native_acquisition_clock import group, fill
from src.backend.backtest_native_channel_sequences import _prepare_loaded_sequence
from src.backend.backtest_native_channel_exit_request import build_native_channel_exit_request
from src.backend.backtest_native_channel_exit_projection import project_native_channel_exit
from src.backend.backtest_native_channel_exit_recovery import reprove_native_channel_exit_rows
from src.market_engine.native_causal_channel_contract import (
    NativeChannelProjection, FEATURE_SCHEMA, COVERAGE_SCHEMA, table_hash, projection_token,
)
from src.trading_runtime.arte_journal_writer import typed_contract_row, verify_typed_contract_row
from src.trading_runtime.arte_native_channel_exit_schema import DECISION, SOURCE_ROW, EXECUTION_ROW


def recovery_case(installed):
    # Controlled source transport, with a fully validated typed projection.
    # This fixture establishes recovery semantics, not real-market certification.
    prepared, policy, decisions, quotes, financial, acquired = inputs(installed)
    packet = prepared.packet
    offset = 14_400_000
    source_request = replace(packet.request, through_day_boundary_ms=packet.request.through_day_boundary_ms + offset)
    rows = pl.from_arrow(packet.rows).with_columns(
        (pl.col('bucket_index') + offset // pl.col('resolution_ms')).cast(pl.UInt32).alias('bucket_index'),
        (pl.col('available_day_boundary_ms') + offset).alias('available_day_boundary_ms'),
        ((pl.col('available_day_boundary_ms') + offset) / 86400000).alias('source_day_fraction')).to_arrow().cast(FEATURE_SCHEMA)
    coverage = []
    for row in packet.coverage.to_pylist():
        subset = pl.from_arrow(rows).filter(pl.col('resolution_ms') == row['resolution_ms']).to_arrow().cast(FEATURE_SCHEMA)
        coverage.append({**row, 'through_day_boundary_ms': source_request.through_day_boundary_ms,
                         'content_hash': table_hash(subset)})
    coverage = pl.DataFrame(coverage).to_arrow().cast(COVERAGE_SCHEMA)
    shifted = NativeChannelProjection(source_request, packet.feature_attempt_id, rows, coverage,
        projection_token(source_request, packet.feature_attempt_id, rows, coverage))
    prepared = _prepare_loaded_sequence(shifted, policy.sequence)
    acquired = replace(acquired, acquisition_day_us=offset * 1000 + 200000,
        first_execution_time=acquired.first_execution_time + timedelta(milliseconds=offset + 200))
    decisions = decisions.with_columns((pl.col('decision_day_ms') + offset).alias('decision_day_ms'))
    quotes = quotes.with_columns((pl.col('decision_day_ms') + offset).alias('decision_day_ms'),
        (pl.col('quote_day_us') + offset * 1000).alias('quote_day_us'))
    request = build_native_channel_exit_request(prepared, decisions, quotes, policy=policy,
        financial=financial, acquisition=acquired, source_entry_intent_id=INTENT)
    as_of = acquired.first_execution_time + timedelta(milliseconds=359800)
    owned = replace(group(), intent_id=INTENT, ticker=financial.ticker,
        submitted_at=acquired.first_execution_time - timedelta(milliseconds=100), updated_at=as_of)
    execution = fill('execution', 5, acquired.first_execution_time, symbol=financial.ticker)
    _, _, _, args = context(installed)
    args.update(as_of=as_of, current_financial=financial)
    projected = project_native_channel_exit(request, owned, (execution,), **args)
    recovery = dict(prepared=prepared, policy=policy, quotes=quotes, group=owned, executions=(execution,),
        session_date=acquired.session_date, **args)
    return projected, recovery


def test_saved_exit_recomputed_from_complete_feature_packet_and_native_fills(installed):
    rows, args = recovery_case(installed)
    request = reprove_native_channel_exit_rows(*rows, **args)
    assert request.acquisition.acquisition_day_us == 14_400_200_000
    assert request.source_plan is args['prepared'].source_plan
    assert request.financial == args['current_financial']


def rehash(contract, row):
    return typed_contract_row(contract, {k: v for k, v in row.items() if k != 'content_hash'})


@pytest.mark.parametrize('defect', ['header_risk', 'source', 'fill', 'quote', 'missing', 'duplicate', 'hash'])
def test_self_consistent_forgery_cannot_replace_certified_source_or_actual_fills(installed, defect):
    (header, sources, fills), args = recovery_case(installed)
    if defect == 'header_risk': header = rehash(DECISION, {**header, 'initial_stop': 0.7})
    if defect == 'source': sources = (rehash(SOURCE_ROW, {**sources[0], 'close_int': sources[0]['close_int'] + 1}),)
    if defect == 'fill': fills = (rehash(EXECUTION_ROW, {**fills[0], 'quantity': 4}),)
    if defect == 'quote': header = rehash(DECISION, {**header, 'bid_int': header['bid_int'] - 1})
    if defect == 'missing': sources = ()
    if defect == 'duplicate': sources = (*sources, sources[0])
    if defect == 'hash': header = {**header, 'content_hash': 'c' * 64}
    with pytest.raises(ValueError): reprove_native_channel_exit_rows(header, sources, fills, **args)


def test_explicit_utc_database_transport_retains_microseconds(installed):
    rows, args = recovery_case(installed)
    canonical = tuple(tuple(verify_typed_contract_row(contract, row) for row in family)
        for contract, family in zip((DECISION, SOURCE_ROW, EXECUTION_ROW), ((rows[0],), rows[1], rows[2])))
    request = reprove_native_channel_exit_rows(canonical[0][0], canonical[1], canonical[2], stored_utc=True, **args)
    assert request.acquisition.first_execution_time.microsecond == 200000

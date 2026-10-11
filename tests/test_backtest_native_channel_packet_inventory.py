"""Packet projection selection preserves exact parent lineage and ownership."""
from dataclasses import replace
from uuid import uuid4

import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit import setup
from src.backend.backtest_market_data import project_market_day_plan, MARKET_DAY_STAGES
from src.backend.backtest_native_channel_packet_inventory import select_parent_projected_native_packets
from src.market_engine.native_causal_channel_contract import COVERAGE_TABLE, issue_source_plan


def case(installed):
    client, _, packet, _, _ = installed
    _, policy, _, _ = setup(installed)
    original = packet.request.market
    parent = replace(original, tickers=('X', 'Y'), units=tuple(
        replace(original.units[0], ticker=ticker, stage=stage)
        for ticker in ('X', 'Y') for stage in MARKET_DAY_STAGES))
    projected = project_market_day_plan(parent, packet.request.tickers)
    rows = pl.from_arrow(client.tables[COVERAGE_TABLE]).with_columns(
        pl.lit(projected.token).alias('market_plan_token'))
    args = dict(market=parent, session_date=packet.request.session_date,
        tickers=packet.request.tickers, policy=policy.sequence,
        producer_source_hash=issue_source_plan(packet).producer_source_hash)
    return rows, args, projected


def test_selects_packet_projection_instead_of_whole_parent(installed):
    rows, args, projected = case(installed)
    selected = select_parent_projected_native_packets(**args, inventories=(rows,))
    assert selected.requests[0][1].market == projected
    assert selected.horizon == rows['through_day_boundary_ms'][0]
    assert selected.excluded_packet_identities == ()


def test_reports_foreign_projection_without_using_its_rows(installed):
    rows, args, _ = case(installed)
    foreign = rows.with_columns(pl.lit(str(uuid4())).alias('feature_attempt_id'),
        pl.lit('f' * 64).alias('market_plan_token'))
    selected = select_parent_projected_native_packets(**args, inventories=(foreign, rows))
    assert len(selected.requests) == 1
    assert selected.excluded_packet_identities == ((foreign['feature_attempt_id'][0],
        'f' * 64, 'different_certified_parent_projection'),)


@pytest.mark.parametrize('defect', ['overlap', 'missing', 'mixed_horizon', 'foreign_only'])
def test_rejects_ambiguous_or_incomplete_packets(installed, defect):
    rows, args, _ = case(installed)
    if defect == 'overlap':
        inventories = (rows, rows.with_columns(pl.lit(str(uuid4())).alias('feature_attempt_id')))
    elif defect == 'missing':
        inventories = (rows.head(rows.height - 1),)
    elif defect == 'mixed_horizon':
        inventories = (rows.with_row_index('_row').with_columns(
            (pl.col('through_day_boundary_ms') + pl.col('_row')).alias(
                'through_day_boundary_ms')).drop('_row'),)
    else:
        inventories = (rows.with_columns(pl.lit('f' * 64).alias('market_plan_token')),)
    with pytest.raises(ValueError):
        select_parent_projected_native_packets(**args, inventories=inventories)

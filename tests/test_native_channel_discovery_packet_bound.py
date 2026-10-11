"""Packet discovery capacity is independent of mandatory ticker count."""
from uuid import uuid4
from dataclasses import replace
import pyarrow as pa
import polars as pl
import pytest
from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit import setup
from src.backend import backtest_native_channel_session_source as source
from src.market_engine.native_causal_channel_contract import issue_source_plan
from src.backend.backtest_market_data import MARKET_DAY_STAGES


@pytest.mark.parametrize('same_parent', [False, True])
def test_attempts_outnumber_tickers_without_weakening_parent_selection(installed, monkeypatch, same_parent):
    _, _, packet, _, _ = installed
    _, policy, _, _ = setup(installed)
    valid = packet.coverage
    attempt = str(uuid4())
    extra = pl.from_arrow(valid).with_columns(pl.lit(attempt).alias('feature_attempt_id'))
    if not same_parent:
        extra = extra.with_columns(pl.lit('f' * 64).alias('market_plan_token'))
    extra = extra.to_arrow().cast(valid.schema)
    combined = pa.concat_tables([valid, extra])
    def read(client, sql, schema, maximum_rows):
        if 'WHERE build_id=' in sql:
            return combined
        return extra if attempt in sql else valid
    monkeypatch.setattr(source, 'read_arrow', read)
    market = replace(packet.request.market, units=tuple(
        replace(packet.request.market.units[0], stage=stage) for stage in MARKET_DAY_STAGES))
    args = dict(market=market, session_date=packet.request.session_date,
        tickers=packet.request.tickers, policy=policy.sequence,
        producer_source_hash=issue_source_plan(packet).producer_source_hash)
    with pytest.raises(ValueError, match='2 packet attempts; declared limit is 1'):
        source._discover_parent_projected_packets(object(), **args, max_packets=1)
    if same_parent:
        with pytest.raises(ValueError):
            source._discover_parent_projected_packets(object(), **args,
                max_packets=source.MAX_DISCOVERY_PACKETS)
    else:
        selected = source._discover_parent_projected_packets(object(), **args,
            max_packets=source.MAX_DISCOVERY_PACKETS)
        assert len(selected.requests) == 1
        assert selected.requests[0][0] == packet.feature_attempt_id
        assert selected.excluded_packet_identities == ((attempt, 'f' * 64,
            'different_certified_parent_projection'),)


def test_empty_inventory_is_reported_separately(installed, monkeypatch):
    _, _, packet, _, _ = installed
    _, policy, _, _ = setup(installed)
    monkeypatch.setattr(source, 'read_arrow', lambda *args: packet.coverage.slice(0, 0))
    with pytest.raises(ValueError, match='no matching packet inventory'):
        source._discover_parent_projected_packets(object(), market=packet.request.market,
            session_date=packet.request.session_date, tickers=packet.request.tickers,
            policy=policy.sequence, producer_source_hash=issue_source_plan(packet).producer_source_hash,
            max_packets=source.MAX_DISCOVERY_PACKETS)

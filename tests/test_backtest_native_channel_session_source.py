"""Installed packet selection with actual compilation and producer fences."""
from dataclasses import replace

import pyarrow as pa
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit import setup
from src.backend.backtest_native_channel_session_source import (
    PreparedNativeChannelSession, prepare_installed_native_channel_session,
    prepare_and_bind_native_channel_session,
    resolve_installed_native_channel_horizon,
)
from src.market_engine.native_causal_channel_contract import COVERAGE_TABLE, issue_source_plan
from src.market_engine.completed_return_insert_authority import InsertAuthorityUnavailable


def session_case(installed, monkeypatch):
    from src.backend import backtest_native_channel_session_source as session_module
    from src.backend import backtest_native_channel_store as store
    client, authority, packet, _, _ = installed
    # The shared fixture controls the market certificate transport; propagate
    # that same verifier to this consumer without changing its production gate.
    monkeypatch.setattr(session_module, 'verify_market_day_plan', store.verify_market_day_plan)
    prepared, policy, _, _ = setup(installed)
    original = client.iter_arrow_record_batches
    # Only discovery transport is controlled. Full packet loading, schema and
    # source certification, producer fence, and sequence compilation are real.
    def batches(sql):
        if f'FROM {COVERAGE_TABLE} WHERE build_id=' in sql:
            assert packet.request.market.build_id in sql
            assert 'AND market_plan_token=' not in sql
            assert packet.request.policy.digest in sql
            assert issue_source_plan(packet).producer_source_hash in sql
            assert 'ticker IN (' in sql
            assert sql.startswith('SELECT ') and 'SETTINGS' not in sql
            client.sql.append(sql)
            return iter(client.tables[COVERAGE_TABLE].to_batches(max_chunksize=3))
        return original(sql)
    monkeypatch.setattr(client, 'iter_arrow_record_batches', batches)
    args = dict(market=packet.request.market, session_date=packet.request.session_date,
        tickers=packet.request.tickers, through_day_boundary_ms=packet.request.through_day_boundary_ms,
        policy=policy.sequence, producer_source_hash=issue_source_plan(packet).producer_source_hash,
        authority=authority, max_packets=1)
    return client, packet, prepared, args


def test_installed_session_compiles_once_and_lookup_has_no_source_reads(installed, monkeypatch):
    client, packet, prior, args = session_case(installed, monkeypatch)
    writes = list(client.writes)
    session = prepare_installed_native_channel_session(client, **args)
    assert type(session) is PreparedNativeChannelSession
    assert len(session.sequences) == 1
    selected = session.sequence_for(packet.request.tickers[0])
    assert selected.source_plan == prior.source_plan
    assert selected.windows.equals(prior.windows)
    reads = len(client.sql)
    assert session.sequence_for(packet.request.tickers[0]) is selected
    assert len(client.sql) == reads and client.writes == writes
    with pytest.raises(ValueError): session.sequence_for('OUTSIDE-SCOPE')
    with pytest.raises(TypeError): session._by_ticker['OUTSIDE-SCOPE'] = selected
    with pytest.raises(ValueError): replace(session, sequences=(selected, selected))


def test_prepare_bind_preserves_full_source_horizon_and_shorter_execution(installed, monkeypatch):
    from tests.test_backtest_native_channel_management_owner import owner_case
    client,packet,_,args=session_case(installed,monkeypatch)
    owner,_,_,_,_=owner_case(installed)
    kwargs=dict(market=args['market'],tickers=args['tickers'],
        source_through_day_boundary_ms=args['through_day_boundary_ms'],
        execution_through_day_boundary_ms=args['through_day_boundary_ms']//2,
        producer_source_hash=args['producer_source_hash'],authority=args['authority'],
        max_packets=1,max_positions=1)
    prepared=prepare_and_bind_native_channel_session(owner,client,**kwargs)
    assert owner._feature_session==(prepared,kwargs['execution_through_day_boundary_ms'],1)
    assert prepared.sequences[0].packet.request.through_day_boundary_ms==args['through_day_boundary_ms']
    reads=len(client.sql)
    with pytest.raises(ValueError): prepare_and_bind_native_channel_session(owner,client,**kwargs)
    assert len(client.sql)==reads
    other,_,_,_,_=owner_case(installed)
    reads=len(client.sql)
    with pytest.raises(ValueError): prepare_and_bind_native_channel_session(other,client,
        **{**kwargs,'execution_through_day_boundary_ms':args['through_day_boundary_ms']+1})
    assert other._feature_session is None and len(client.sql)==reads


def test_actual_projected_packet_loads_through_larger_parent(unit, monkeypatch):
    from pipelines.market_sip.events.native_causal_channel_producer import produce_native_channels
    from pipelines.market_sip.events.native_channel_campaign import publish_packet
    from src.backend.backtest_market_data import project_market_day_plan, MARKET_DAY_STAGES
    from src.backend import backtest_native_channel_session_source as session_module
    from src.backend import backtest_native_channel_store as store
    from src.backend.backtest_native_channel_sequences import prepare_installed_native_channel_sequence
    client, authority, original, _ = unit
    market = original.request.market
    parent = replace(market, tickers=('X', 'Y'), units=tuple(
        replace(market.units[0], ticker=ticker, stage=stage)
        for ticker in ('X', 'Y') for stage in MARKET_DAY_STAGES))
    projected = project_market_day_plan(parent, original.request.tickers)
    packet = produce_native_channels(replace(original.request, market=projected),
        original.feature_attempt_id, client)
    publish_packet(client, packet, authority=authority, producer_client=client.writer)
    import polars as pl
    decisions = pl.from_arrow(packet.coverage).select(
        'build_id', 'session_date', 'ticker', 'attempt_id').unique().join(
        pl.DataFrame({'decision_day_ms': [360000]}), how='cross')
    fixture = (client, authority, packet, decisions, None)
    prior, policy, _, _ = setup(fixture)
    monkeypatch.setattr(session_module, 'verify_market_day_plan', store.verify_market_day_plan)
    transport = client.iter_arrow_record_batches
    def batches(sql):
        if f'FROM {COVERAGE_TABLE} WHERE build_id=' in sql:
            assert 'AND market_plan_token=' not in sql
            client.sql.append(sql)
            return iter(client.tables[COVERAGE_TABLE].to_batches(max_chunksize=3))
        return transport(sql)
    monkeypatch.setattr(client, 'iter_arrow_record_batches', batches)
    args = dict(market=parent, session_date=packet.request.session_date,
        tickers=packet.request.tickers, policy=policy.sequence,
        producer_source_hash=issue_source_plan(packet).producer_source_hash)
    writes = tuple(client.writes)
    horizon = resolve_installed_native_channel_horizon(client, **args)
    loaded = prepare_installed_native_channel_session(client, **args,
        through_day_boundary_ms=horizon, authority=authority, max_packets=1)
    independent = prepare_installed_native_channel_sequence(client, packet.request,
        packet.feature_attempt_id, policy=policy.sequence,
        producer_source_hash=args['producer_source_hash'], authority=authority)
    assert loaded.sequences[0].source_plan == independent.source_plan == prior.source_plan
    assert loaded.sequences[0].windows.equals(independent.windows)
    assert loaded.sequences[0].packet.request.market == projected
    assert tuple(client.writes) == writes


def test_horizon_discovery_requires_unique_complete_inventory(installed,monkeypatch):
    client,packet,_,args=session_case(installed,monkeypatch)
    kwargs={key:args[key] for key in ('market','session_date','tickers','policy','producer_source_hash')}
    assert resolve_installed_native_channel_horizon(client,**kwargs)==packet.request.through_day_boundary_ms
    table=client.tables[COVERAGE_TABLE]
    client.tables[COVERAGE_TABLE]=pa.concat_tables([table,table])
    with pytest.raises(ValueError): resolve_installed_native_channel_horizon(client,**kwargs)
    client.tables[COVERAGE_TABLE]=table.slice(0,table.num_rows-1)
    with pytest.raises(ValueError): resolve_installed_native_channel_horizon(client,**kwargs)
    import polars as pl
    client.tables[COVERAGE_TABLE]=pl.from_arrow(table).with_row_index('_row').with_columns(
        pl.when(pl.col('_row')==0).then(pl.col('through_day_boundary_ms')+100)
        .otherwise(pl.col('through_day_boundary_ms')).alias('through_day_boundary_ms')
    ).drop('_row').to_arrow().cast(table.schema)
    with pytest.raises(ValueError): resolve_installed_native_channel_horizon(client,**kwargs)


@pytest.mark.parametrize('defect', ['missing', 'ambiguous', 'foreign', 'pending'])
def test_session_rejects_incomplete_or_ambiguous_installed_authority(installed, monkeypatch, defect):
    client, packet, _, args = session_case(installed, monkeypatch)
    coverage = client.tables[COVERAGE_TABLE]
    if defect == 'missing':
        client.tables[COVERAGE_TABLE] = coverage.slice(0, 0)
    elif defect == 'ambiguous':
        client.tables[COVERAGE_TABLE] = pa.concat_tables([coverage, coverage])
    elif defect == 'foreign':
        column = coverage.schema.get_field_index('market_plan_token')
        client.tables[COVERAGE_TABLE] = coverage.set_column(column, coverage.schema.field(column),
            pa.array(['f' * 64] * coverage.num_rows))
    else:
        authority = args['authority']
        path = authority.namespace + '/' + packet.feature_attempt_id + '/gate'
        _, stat = authority.keeper.get(path)
        authority.keeper.set(path, b'{"state":"fresh"}', version=stat.version)
    writes = list(client.writes)
    with pytest.raises((ValueError, InsertAuthorityUnavailable)):
        prepare_installed_native_channel_session(client, **args)
    assert client.writes == writes


@pytest.mark.parametrize('change', [dict(tickers=('OUTSIDE-SCOPE',)), dict(max_packets=0),
    dict(through_day_boundary_ms=-1), dict(tickers=())])
def test_foreign_or_unbounded_session_inputs_fail_before_discovery(installed, monkeypatch, change):
    client, _, _, args = session_case(installed, monkeypatch)
    before = len(client.sql)
    with pytest.raises(ValueError): prepare_installed_native_channel_session(client, **{**args, **change})
    assert len(client.sql) == before

"""Installed packet selection with actual compilation and producer fences."""
from dataclasses import replace

import pyarrow as pa
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit import setup
from src.backend.backtest_native_channel_session_source import (
    PreparedNativeChannelSession, prepare_installed_native_channel_session,
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
            assert packet.request.market.token in sql
            assert packet.request.policy.digest in sql
            assert issue_source_plan(packet).producer_source_hash in sql
            assert 'through_day_boundary_ms=' in sql and 'ticker IN (' in sql
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

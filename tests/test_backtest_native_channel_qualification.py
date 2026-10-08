"""Installed reader and columnar qualification with controlled transport only."""
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from pipelines.market_sip.events.native_channel_campaign import publish_packet
from src.backend.backtest_native_channel_qualification import (
    qualify_installed_native_channel_decisions,
)
from src.market_engine.completed_return_insert_authority import InsertAuthorityUnavailable
from src.market_engine.native_causal_channel_contract import issue_source_plan
from src.trading_runtime.native_channel_qualification import (
    NativeChannelBand, NativeChannelQualificationPolicy,
)
from tests.test_native_channel_campaign import unit  # noqa: F401


@pytest.fixture
def installed(unit):
    client, authority, packet, _ = unit
    publish_packet(client, packet, authority=authority, producer_client=client.writer)
    scope = pl.from_arrow(packet.coverage).select(
        'build_id', 'session_date', 'ticker', 'attempt_id').unique()
    decisions = scope.join(pl.DataFrame({'decision_day_ms': [360000, 60000, 420000, 480000]}),
                           how='cross')
    policy = NativeChannelQualificationPolicy(packet.request.policy,
        (NativeChannelBand(60000, 'close_return', 0.0, None),))
    return client, authority, packet, decisions, policy


def call(inputs, *, decisions=None, mandatory=None, policy=None, producer=None):
    client, authority, packet, original, declared = inputs
    left = original if decisions is None else decisions
    mask = np.ones(left.height, dtype=bool) if mandatory is None else mandatory
    return qualify_installed_native_channel_decisions(client, packet.request,
        packet.feature_attempt_id, left, mask, policy=declared if policy is None else policy,
        producer_source_hash=issue_source_plan(packet).producer_source_hash if producer is None else producer,
        authority=authority)


def test_real_installed_load_preserves_order_and_mandatory_rejection(installed):
    expected = [True, False, False, True]
    assert call(installed).tolist() == expected
    mask = np.array([False, True, True, True])
    original = mask.copy()
    output = call(installed, mandatory=mask)
    assert output.tolist() == [False, False, False, True]
    assert np.array_equal(mask, original)
    assert not output.flags.writeable
    with pytest.raises(ValueError):
        output.setflags(write=True)
    assert installed[3]['decision_day_ms'].to_list() == [360000, 60000, 420000, 480000]
    assert len(installed[0].writes) == 2  # Qualification never writes.


def test_research_labels_cannot_change_loaded_qualification(installed):
    left = installed[3].with_columns(pl.lit(999999.0).alias('future_reward'))
    assert np.array_equal(call(installed), call(installed, decisions=left))


@pytest.mark.parametrize('field,value', [('ticker', 'FOREIGN'), ('session_date', '2026-08-05'),
    ('build_id', 'f' * 64), ('attempt_id', '33333333-3333-4333-8333-333333333333')])
def test_foreign_decision_identity_fails_closed(installed, field, value):
    left = installed[3].with_columns(pl.lit(value).alias(field))
    with pytest.raises(ValueError, match='outside installed native coverage'):
        call(installed, decisions=left)


@pytest.mark.parametrize('clock', [-100, 600100, 360001])
def test_invalid_or_uncovered_clock_fails_before_source_reads(installed, clock):
    before = len(installed[0].sql)
    left = installed[3].with_columns(pl.lit(clock, dtype=pl.Int64).alias('decision_day_ms'))
    with pytest.raises(ValueError, match='identity or declared clock'):
        call(installed, decisions=left)
    assert len(installed[0].sql) == before


def test_unresolved_dispatch_cannot_qualify(installed):
    client, authority, packet, _, _ = installed
    path = authority.namespace + '/' + packet.feature_attempt_id + '/gate'
    _, stat = authority.keeper.get(path)
    authority.keeper.set(path, b'{"state":"fresh"}', version=stat.version)
    with pytest.raises(InsertAuthorityUnavailable):
        call(installed)


def test_rule_input_policy_cannot_drift(installed):
    policy = installed[4]
    changed = replace(policy, inputs=replace(policy.inputs, participation_lookback_bars=4))
    with pytest.raises(ValueError, match='input policy differs'):
        call(installed, policy=changed)


def test_wrong_producer_and_untyped_mask_cannot_qualify(installed):
    with pytest.raises(ValueError, match='producer differs'):
        call(installed, producer='f' * 64)
    with pytest.raises(ValueError, match='Boolean eligibility'):
        call(installed, mandatory=np.ones(4, dtype=np.int64))


def test_caller_mask_alias_cannot_change_during_source_read(installed, monkeypatch):
    from src.backend import backtest_native_channel_qualification as module
    original = module.load_declared_native_channels
    mask = np.array([False, True, True, True])
    def read(*args, **kwargs):
        mask[:] = True
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'load_declared_native_channels', read)
    assert call(installed, mandatory=mask).tolist() == [False, False, False, True]


def test_empty_decisions_still_require_installed_dependency(installed):
    assert call(installed, decisions=installed[3].head(0)).shape == (0,)

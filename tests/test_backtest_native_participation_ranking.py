"""Actual installed reader contracts with controlled storage/Keeper transport."""
import numpy as np
import polars as pl
import pytest

from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy
from src.backend.backtest_native_participation_ranking import prepare_installed_participation_scores
from src.market_engine.completed_return_insert_authority import InsertAuthorityUnavailable
from src.market_engine.native_causal_channel_contract import issue_source_plan
from tests.test_backtest_native_channel_qualification import installed  # noqa: F401
from tests.test_native_channel_campaign import unit  # noqa: F401


def call(inputs, *, decisions=None, mandatory=None):
    client, authority, packet, original, _ = inputs
    left = original if decisions is None else decisions
    mask = np.ones(left.height, dtype=bool) if mandatory is None else mandatory
    return prepare_installed_participation_scores(client, packet.request,
        packet.feature_attempt_id, left, mask,
        policy=ParticipationRankingPolicy(((60000, 1.),), .5, .5, 1., 1, 100),
        producer_source_hash=issue_source_plan(packet).producer_source_hash, authority=authority)


def test_installed_ranking_preserves_mandatory_mask_order_and_immutable_arrays(installed):
    mask = np.array([False, True, True, True])
    original = mask.copy()
    output = call(installed, mandatory=mask)
    assert np.array_equal(output.mandatory_eligible, original)
    assert np.array_equal(mask, original)
    assert not output.available[0] and np.isnan(output.scores[0])
    assert output.scores.shape == (4,)
    assert output.available.any()
    assert np.isfinite(output.scores[output.available]).all()
    for value in output:
        assert not value.flags.writeable
        with pytest.raises(ValueError):
            value.setflags(write=True)
    assert installed[3]['decision_day_ms'].to_list() == [360000, 60000, 420000, 480000]
    assert len(installed[0].writes) == 2


def test_labels_cannot_change_scores(installed):
    before = call(installed)
    after = call(installed, decisions=installed[3].with_columns(pl.lit(999.).alias('future_reward')))
    for a, b in zip(before, after):
        np.testing.assert_array_equal(a, b)


def test_foreign_identity_and_invalid_clock_fail_closed(installed):
    with pytest.raises(ValueError, match='outside installed native coverage'):
        call(installed, decisions=installed[3].with_columns(pl.lit('FOREIGN').alias('ticker')))
    with pytest.raises(ValueError, match='declared clock'):
        call(installed, decisions=installed[3].with_columns(pl.lit(360001, dtype=pl.Int64).alias('decision_day_ms')))


def test_unresolved_dispatch_cannot_supply_scores(installed):
    _, authority, packet, _, _ = installed
    path = authority.namespace + '/' + packet.feature_attempt_id + '/gate'
    _, stat = authority.keeper.get(path)
    authority.keeper.set(path, b'{"state":"fresh"}', version=stat.version)
    with pytest.raises(InsertAuthorityUnavailable):
        call(installed)


def test_caller_mask_is_detached_before_source_read(installed, monkeypatch):
    from src.backend import backtest_native_participation_ranking as module
    original = module.load_declared_native_channels
    mask = np.array([False, True, True, True])
    def read(*args, **kwargs):
        mask[:] = True
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'load_declared_native_channels', read)
    assert call(installed, mandatory=mask).mandatory_eligible.tolist() == [False, True, True, True]


def test_empty_decisions_still_verify_installed_dependency(installed):
    output = call(installed, decisions=installed[3].head(0))
    assert all(value.shape == (0,) for value in output)


def test_untyped_mask_is_rejected_before_read(installed):
    before = len(installed[0].sql)
    with pytest.raises(ValueError, match='Boolean eligibility'):
        call(installed, mandatory=np.ones(4, dtype=np.int64))
    assert len(installed[0].sql) == before

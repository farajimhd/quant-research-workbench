from copy import deepcopy
import pytest
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.run_ranked_teacher_underfit import passes


def report():
    names = ('wait', 'enter_long', 'hold', 'exit_long')
    return dict(action_class_counts=dict.fromkeys(names, 10),
        action_predicted_class_counts=dict.fromkeys(names, 10),
        action_class_recall=dict.fromkeys(names, 1.),
        forecast_label_metrics=[dict(confusion=[[10 if i == j else 0 for j in range(4)] for i in range(4)]) for _ in range(5)],
        allocation_ratio_mae=.01, allocation_targets=10, allocation_weight=10., allocation_error_sum=.1,
        action_quality_weights=dict(ENTRY=10.,EXIT=10.), action_quality_error_sums=dict(ENTRY=.1,EXIT=.1),
        forecast_quality_weights=[dict(ENTRY=10.,EXIT=10.) for _ in range(5)],
        forecast_quality_error_sums=[dict(ENTRY=.1,EXIT=.1) for _ in range(5)],
        action_quality_mae=dict(ENTRY=.01, EXIT=.01),
        action_quality_counts=dict(ENTRY=10, EXIT=10),
        forecast_quality_mae=[dict(ENTRY=.01, EXIT=.01) for _ in range(5)],
        forecast_quality_counts=[dict(ENTRY=10, EXIT=10) for _ in range(5)])


def test_same_checkpoint_pool_passes_complete_gate():
    pooled = pool_gate_metrics([report(), report()])
    assert passes(pooled)
    assert pooled['allocation_targets'] == 20
    assert pooled['action_class_counts']['enter_long'] == 20


def test_counts_prevent_averaging_away_rare_action_failure():
    rare = report(); large = report()
    rare['action_class_counts']['enter_long'] = 1
    rare['action_predicted_class_counts']['enter_long'] = 1
    large['action_class_counts']['enter_long'] = 100
    large['action_predicted_class_counts']['enter_long'] = 100
    large['action_class_recall']['enter_long'] = 0.
    pooled = pool_gate_metrics([rare, large])
    assert pooled['action_class_f1']['enter_long'] == pytest.approx(1/101)
    assert not passes(pooled)


def test_regression_uses_sample_weights_and_rejects_missing_observed_errors():
    a = report(); b = report()
    b['allocation_ratio_mae'] = .09; b['allocation_targets'] = 30
    b['allocation_weight'] = 1.; b['allocation_error_sum'] = .09
    assert pool_gate_metrics([a, b])['allocation_ratio_mae'] == pytest.approx(.19/11)
    b['allocation_ratio_mae'] = None
    with pytest.raises(ValueError, match='finite errors'):
        pool_gate_metrics([a, b])


def test_absent_global_future_class_fails_gate_and_bad_counts_rejected():
    r = report()
    r['forecast_label_metrics'][4]['confusion'][3][3] = 0
    assert not passes(pool_gate_metrics([r]))
    bad = deepcopy(r); bad['forecast_label_metrics'][0]['confusion'][0][0] = .5
    with pytest.raises(ValueError, match='confusion'):
        pool_gate_metrics([bad])


def test_inconsistent_current_evidence_rejected():
    r = report(); r['action_class_recall']['enter_long'] = .123
    with pytest.raises(ValueError, match='exact nonnegative count'):
        pool_gate_metrics([r])


def test_missing_or_inconsistent_weight_receipts_cannot_pass():
    r = report(); del r['allocation_weight']
    with pytest.raises(KeyError): pool_gate_metrics([r])
    r = report(); r['forecast_quality_error_sums'][2]['ENTRY'] = .2
    with pytest.raises(ValueError, match='disagree'): pool_gate_metrics([r])


def test_all_regression_heads_pool_fractional_weights():
    a = report(); b = report()
    for label in ('ENTRY','EXIT'):
        b['action_quality_mae'][label] = .09
        b['action_quality_weights'][label] = .25
        b['action_quality_error_sums'][label] = .0225
        for h in range(5):
            b['forecast_quality_mae'][h][label] = .09
            b['forecast_quality_weights'][h][label] = .25
            b['forecast_quality_error_sums'][h][label] = .0225
    r = pool_gate_metrics([a,b])
    assert r['action_quality_mae']['ENTRY'] == pytest.approx(.1225/10.25)
    assert all(h['EXIT'] == pytest.approx(.1225/10.25) for h in r['forecast_quality_mae'])

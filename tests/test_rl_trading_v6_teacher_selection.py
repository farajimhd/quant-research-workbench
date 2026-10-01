import pytest
from research.rl_trading.v6.teacher_selection import teacher_validation_score, selection_key


def summary(day, actual, predicted, correct, exact, loss):
    return dict(day=day,decisions=100,mean_loss=loss,
        action_class_counts={'enter_long':actual},
        action_predicted_class_counts={'enter_long':predicted},
        action_class_recall={'enter_long':correct/actual}, entry_token_accuracy=exact/actual)


def test_teacher_selection_counts_exact_ticker_not_aggregate_hold_accuracy():
    rows=[summary('2026-08-24',10,5,5,2,1.), summary('2026-08-25',20,10,10,8,2.)]
    score=teacher_validation_score(rows)
    assert score['exact_entry_f1']==pytest.approx(20/45)
    assert score['entry_class_f1']==pytest.approx(30/45)
    assert score['mean_label_loss']==1.5
    weaker={**score,'exact_entry_f1':.1,'mean_label_loss':.01}
    assert selection_key(score)>selection_key(weaker)


def test_teacher_selection_rejects_sealed_test_or_duplicate_development_days():
    row=summary('2026-08-24',10,0,0,0,1.)
    for wrong in ('2026-08-24','2026-08-26','2026-08-05'):
        with pytest.raises(ValueError):
            teacher_validation_score([row,{**row,'day':wrong}])


def test_no_entry_predictions_get_zero_f1_instead_of_high_wait_accuracy_credit():
    rows=[summary(day,10,0,0,0,.01) for day in ('2026-08-24','2026-08-25')]
    score=teacher_validation_score(rows)
    assert score['exact_entry_f1']==score['entry_class_f1']==0.

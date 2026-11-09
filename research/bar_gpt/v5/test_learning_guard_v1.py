"""Learning review must distinguish persistent evidence from noisy validation."""

from research.bar_gpt.v5.learning_guard_v1 import assess


def row(train, val, probe):
    return dict(
        training=dict(loss=train), validation=dict(loss=val), probe=dict(loss=probe)
    )


def test_learns_and_does_not_stop_on_one_noisy_epoch():
    history = [row(0.9, 0.8, 0.8), row(0.7, 0.79, 0.79), row(0.6, 0.81, 0.78)]
    assert assess(history, 1.0)["action"] == "continue"


def test_no_learning_requires_three_completed_epochs():
    assert assess([row(1.0, 1.0, 1.0)] * 2, 1.0)["action"] == "continue"
    assert assess([row(1.0, 1.0, 1.0)] * 3, 1.0)["reason"] == "no measurable learning"


def test_persistent_overfit_preserves_an_earlier_best():
    history = [
        row(0.8, 0.7, 0.7),
        row(0.7, 0.74, 0.72),
        row(0.6, 0.75, 0.73),
        row(0.5, 0.76, 0.74),
    ]
    assert assess(history, 1.0)["reason"] == "persistent overfit"


def test_nonfinite_and_gross_divergence_stop():
    assert assess([row(1.0, float("nan"), 1.0)], 1.0)["action"] == "stop"
    assert assess([row(1.0, 2.0, 2.0)], 1.0)["reason"] == "gross divergence"


def test_new_domain_history_reports_actual_epoch_with_eight_validation_policy():
    history = [dict(epoch=26, **row(0.8, 0.7, 0.7))]
    for epoch in range(27, 34):
        history.append(dict(epoch=epoch, **row(0.6, 0.75, 0.72)))
    assert assess(history, 1.0, overfit_patience=8)["action"] == "continue"
    history.append(dict(epoch=34, **row(0.5, 0.76, 0.73)))
    decision = assess(history, 1.0, overfit_patience=8)
    assert decision["reason"] == "persistent overfit" and decision["best_epoch"] == 26


def test_five_consecutive_overfit_validations_and_independent_no_learning_guard():
    history = [row(0.8, 0.7, 0.7)]
    for _ in range(4):
        history.append(row(0.6, 0.75, 0.72))
        assert assess(history, 1.0, overfit_patience=5)["action"] == "continue"
    history.append(row(0.5, 0.76, 0.73))
    assert assess(history, 1.0, overfit_patience=5)["reason"] == "persistent overfit"
    assert (
        assess([row(1.0, 1.0, 1.0)] * 3, 1.0, overfit_patience=5)["reason"]
        == "no measurable learning"
    )
    # A non-worsening validation interrupts the five-consecutive requirement.
    history[-1] = row(0.5, 0.705, 0.7)
    assert assess(history, 1.0, overfit_patience=5)["action"] == "continue"

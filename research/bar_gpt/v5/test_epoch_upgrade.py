"""Operational upgrades must wait for complete, healthy epoch boundaries."""

from research.bar_gpt.v5.run_epoch_upgrade import boundary_ready


def test_boundary_requires_completed_history_and_positive_review():
    status = dict(epoch=9, stage="starting train loader")
    review = dict(epoch=8, action="continue")
    results = dict(history=[dict(epoch=8)])
    assert boundary_ready(status, review, results, 8)
    assert not boundary_ready(dict(status, stage="training"), review, results, 8)
    assert not boundary_ready(status, dict(review, action="stop"), results, 8)
    assert not boundary_ready(status, review, dict(history=[dict(epoch=7)]), 8)

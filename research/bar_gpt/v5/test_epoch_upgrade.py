"""Operational upgrades must wait for complete, healthy epoch boundaries."""

import pytest
from research.bar_gpt.v5.run_epoch_upgrade import boundary_ready, updated_launcher


def test_boundary_requires_completed_history_and_positive_review():
    status = dict(epoch=9, stage="starting train loader")
    review = dict(epoch=8, action="continue")
    results = dict(history=[dict(epoch=8)])
    assert boundary_ready(status, review, results, 8)
    assert not boundary_ready(dict(status, stage="training"), review, results, 8)
    assert not boundary_ready(status, dict(review, action="stop"), results, 8)
    assert not boundary_ready(status, review, dict(history=[dict(epoch=7)]), 8)


def test_validation_boundary_requires_prior_review_and_validation_stage():
    review = dict(epoch=50, action="continue")
    results = dict(history=[dict(epoch=50)])
    for stage in ("starting validation loader", "measuring validation", "validation"):
        status = dict(epoch=51, stage=stage)
        assert boundary_ready(status, review, results, 51, before_validation=True)
    for status, verdict, history in (
        (dict(epoch=51, stage="training"), review, results),
        (dict(epoch=52, stage="validation"), review, results),
        (dict(epoch=51, stage="learning probe"), review, results),
        (dict(epoch=51, stage="validation"), dict(epoch=50, action="stop"), results),
        (dict(epoch=51, stage="validation"), review, dict(history=[])),
    ):
        assert not boundary_ready(status, verdict, history, 51, before_validation=True)


def test_explicit_prepared_launcher_rebinds_latest_state_without_science_flags():
    text = """$env:PYTHONPATH='D:/codes/oldrevision'
$arguments=@('--source-revision','oldrevision','--resume-training',"$entry/checkpoint_prepared.pt",'--data-transition-proof',"$entry/proof.json",'--overfit-patience','8')
@{resume="$entry/checkpoint_prepared.pt";source_revision='oldrevision'}
"""
    updated = updated_launcher(
        text, "oldrevision", "newrevision", "D:/runtime/train-06/checkpoint_last.pt"
    )
    assert "'--resume-training','D:/runtime/train-06/checkpoint_last.pt'" in updated
    assert "--data-transition-proof" not in updated
    assert "resume='D:/runtime/train-06/checkpoint_last.pt'" in updated
    assert "'--overfit-patience','8'" in updated and "oldrevision" not in updated
    with pytest.raises(ValueError):
        updated_launcher(
            text.replace("'--resume-training'", "'--foreign'"),
            "oldrevision",
            "newrevision",
            "D:/x.pt",
        )
    with pytest.raises(ValueError):
        updated_launcher(text, "unexpected", "newrevision")

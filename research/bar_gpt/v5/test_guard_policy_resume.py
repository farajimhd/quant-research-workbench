"""Stop-policy amendment preserves every scientific contract and prior best file."""

from copy import deepcopy
import json

import pytest
import torch

from research.bar_gpt.v5.campaign_v1 import (
    compatible_resume_contract,
    preserve_prior_best,
)


def test_only_overfit_patience_can_differ_in_a_resume_contract():
    saved = dict(
        model="large",
        split_hash="sealed",
        peak=0.001,
        learning_guard=dict(patience=3, probe_steps=32),
    )
    current = deepcopy(saved)
    current["learning_guard"]["overfit_patience"] = 5
    assert compatible_resume_contract(saved, current)
    assert "overfit_patience" not in saved["learning_guard"]
    for field, value in (
        ("model", "small"),
        ("split_hash", "changed"),
        ("peak", 0.002),
    ):
        changed = dict(current, **{field: value})
        assert not compatible_resume_contract(saved, changed)
    current["learning_guard"]["patience"] = 5
    assert not compatible_resume_contract(saved, current)


def test_best_checkpoint_follows_ancestry_and_uses_the_saved_epoch(tmp_path):
    first, second, output = (
        tmp_path / name for name in ("train-02", "train-03", "train-04")
    )
    for directory in (first, second, output):
        directory.mkdir()
    contract = dict(model="large", learning_guard=dict(patience=3))
    state = dict(contract=contract, best_validation=0.13)
    source = first / "checkpoint_epoch_010.pt"
    torch.save(state, source)
    # A later best alias must not replace the earlier best of the resumed epoch.
    torch.save(dict(state, best_validation=0.12), first / "checkpoint_best.pt")
    (second / "manifest.json").write_text(
        json.dumps(dict(resume=str(first / "checkpoint_last.pt")))
    )
    changed = deepcopy(contract)
    changed["learning_guard"]["overfit_patience"] = 5
    preserve_prior_best(second / "checkpoint_last.pt", output, 0.13, changed, 10)
    assert (output / "checkpoint_best.pt").read_bytes() == source.read_bytes()
    assert json.loads((output / "best-checkpoint-origin.json").read_text())[
        "source"
    ] == str(source)
    with pytest.raises(ValueError, match="Cannot locate"):
        preserve_prior_best(second / "checkpoint_last.pt", output, 0.11, changed, 10)

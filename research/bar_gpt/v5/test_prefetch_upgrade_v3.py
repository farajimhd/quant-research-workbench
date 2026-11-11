"""Boundary resume must preserve science and refuse partial training."""

from copy import deepcopy
from pathlib import Path
from .run_prefetch_upgrade_v3 import boundary_ready, resume_command
from .run_encoder_v3 import arguments
from .wandb_identity_v1 import signature


def test_only_complete_training_boundary_is_accepted():
    saved = dict(
        phase="validation",
        epoch=0,
        step=42,
        scheduler=dict(updates=42),
        history=[],
        training_metrics=dict(complete_pass=True, loss=0.2),
    )
    assert boundary_ready(saved, dict(epoch=1))
    for key, value in (("phase", "train"), ("step", 0), ("epoch", 2)):
        changed = dict(saved, **{key: value})
        assert not boundary_ready(changed, dict(epoch=1))
    changed = deepcopy(saved)
    changed["training_metrics"]["complete_pass"] = False
    assert not boundary_ready(changed, dict(epoch=1))
    changed = dict(saved, history=[dict(learning_guard=dict(action="stop"))])
    assert not boundary_ready(changed, dict(epoch=1))


def test_resume_keeps_scientific_reporting_identity(tmp_path):
    base = vars(
        arguments(
            [
                "--output",
                str(tmp_path / "parent"),
                "--mode",
                "train",
                "--model",
                "large",
                "--workers",
                "16",
                "--epochs",
                "50",
                "--scheduler",
                "cosine",
                "--learning-rate",
                ".001",
                "--warmup-updates",
                "0",
                "--train-steps",
                "0",
                "--validation-steps",
                "0",
                "--checkpoint-every",
                "0",
                "--attention-dropout",
                "0",
                "--learning-guard",
                "--overfit-patience",
                "8",
                "--split-manifest",
                str(tmp_path / "split.json"),
            ]
        )
    )
    base["mask_rate"] = 0.6
    command = resume_command(
        base, tmp_path / "checkpoint_last.pt", tmp_path / "new", "revision"
    )
    resumed = vars(arguments(command[5:]))
    resumed["mask_rate"] = 0.6
    normalize = lambda row: {
        k: str(v) if isinstance(v, Path) else v for k, v in row.items()
    }
    assert signature(normalize(base)) == signature(normalize(resumed))
    assert resumed["workers"] == 16 and resumed["shard_prefetch"] == 32
    assert resumed["resume"] == tmp_path / "checkpoint_last.pt"

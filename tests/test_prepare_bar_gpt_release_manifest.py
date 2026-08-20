from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = REPO_ROOT / "scripts" / "prepare_bar_gpt_release_manifest.py"
    spec = importlib.util.spec_from_file_location("prepare_bar_gpt_release_manifest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_selection_uses_highest_immutable_marker(tmp_path: Path) -> None:
    module = _module()
    v2 = tmp_path / "v2" / "run" / "checkpoints"
    v3 = tmp_path / "v3" / "run" / "checkpoints"
    v2.mkdir(parents=True)
    v3.mkdir(parents=True)
    (v2 / "checkpoint_latest.pt").touch()
    (v2 / "checkpoint_latest-bk-chunk1-100samples.pt").touch()
    selected_v2 = v2 / "checkpoint_latest-bk-chunk2-200samples.pt"
    selected_v2.touch()
    (v3 / "checkpoint_best_global.pt").touch()
    (v3 / "checkpoint_global_validation_origins_000000000100.pt").touch()
    selected_v3 = v3 / "checkpoint_global_validation_origins_000000000200.pt"
    selected_v3.touch()

    assert module._candidate(tmp_path, "v2") == (selected_v2.resolve(), 200)
    assert module._candidate(tmp_path, "v3") == (selected_v3.resolve(), 200)


def test_checkpoint_selection_fails_closed_without_immutable_checkpoint(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "v2" / "run"
    path.mkdir(parents=True)
    (path / "checkpoint_latest.pt").touch()

    try:
        module._candidate(tmp_path, "v2")
    except RuntimeError as error:
        assert "no immutable v2 checkpoint" in str(error)
    else:
        raise AssertionError("changing latest checkpoint must not be selected")

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research.mlops.wandb_utils import init_wandb


class WandbUtilsTest(unittest.TestCase):
    def test_console_capture_can_be_disabled_without_disabling_run(self) -> None:
        captured: dict[str, object] = {}

        class Settings:
            def __init__(self, **kwargs: object) -> None:
                captured["settings"] = kwargs

        def init(**kwargs: object) -> object:
            captured["init"] = kwargs
            return SimpleNamespace(id="run-id", dir="wandb-dir")

        fake_wandb = SimpleNamespace(
            Settings=Settings,
            init=init,
            login=lambda **_kwargs: None,
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules,
            {"wandb": fake_wandb},
        ), patch.dict(
            os.environ,
            {"WANDB_API_KEY": "test-key"},
        ):
            run = init_wandb(
                entity="entity",
                project="project",
                run_name="run",
                config={},
                run_dir=Path(directory),
                mode="online",
                timeout_seconds=10,
                capture_console=False,
            )

        self.assertEqual(getattr(run, "id", None), "run-id")
        settings = captured["settings"]
        init_arguments = captured["init"]
        self.assertIsInstance(settings, dict)
        self.assertIsInstance(init_arguments, dict)
        assert isinstance(settings, dict) and isinstance(init_arguments, dict)
        self.assertEqual(settings["console"], "off")
        self.assertEqual(init_arguments["mode"], "online")


if __name__ == "__main__":
    unittest.main()

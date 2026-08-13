from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research.bar_gpt.v2.config import BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT
from research.bar_gpt.v2.run_train_model_comparison import (
    COMPARISON_RUNS,
    trainer_argv as baseline_trainer_argv,
)
from research.bar_gpt.v2.run_train_repetition_comparison import (
    REPETITION_EPOCHS,
    REPETITION_MANIFEST_NAME,
    _distribution_audit,
    _launcher_command,
    main,
    repetition_run_name,
    select_repetition_subset,
    trainer_argv,
)
from research.bar_gpt.v2.train import (
    _epoch_boundary_evaluation_namespace,
    parse_args as parse_training_args,
)


def _row(index: int) -> dict[str, object]:
    ticker_index = index % 8
    month_index = (index // 8) % 8
    regime = (index // 64) % 2
    phase_index = (index // 128) % 3
    length_index = (index // 384) % 4
    origins = (512, 1_536, 2_560, 4_096)[length_index]
    ticker = f"T{ticker_index:02d}"
    year = 2019 + month_index // 4
    month = month_index % 4 + 1
    return {
        "unit_key": f"{ticker}:{year}-{month:02d}",
        "session_index": index // 16,
        "block_index": index % 16,
        "origins": origins,
        "ticker": ticker,
        "local_date": f"{year}-{month:02d}-{index % 27 + 1:02d}",
        "activity_regime": regime,
        "session_phase": ("premarket", "regular_midday", "after_hours")[phase_index],
        "has_condition_target": False,
        "unit_index": index // 16,
        "block_offset": index,
    }


class RepetitionComparisonTests(unittest.TestCase):
    def test_four_pass_launcher_preserves_baseline_model_and_metric_contracts(self) -> None:
        for model_size in COMPARISON_RUNS:
            baseline = parse_training_args(
                baseline_trainer_argv(model_size, run_stamp="baseline", wandb_mode="offline")
            )
            repeated = parse_training_args(
                trainer_argv(model_size, run_stamp="repeat", wandb_mode="offline")
            )
            self.assertEqual(repeated.epochs, REPETITION_EPOCHS)
            self.assertTrue(repeated.full_validation_final_epoch_only)
            self.assertTrue(repeated.experiment_manifest.endswith(REPETITION_MANIFEST_NAME))
            self.assertEqual(repeated.wandb_project, BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT)
            for name in (
                "d_model",
                "n_layers",
                "n_heads",
                "n_kv_heads",
                "batch_size",
                "gradient_accumulation_steps",
                "offline_length_bucket_batches",
                "seed",
                "learning_rate",
                "warmup_samples",
                "scheduler_mode",
                "logging_samples",
                "training_metrics_interval_samples",
                "validation_interval_samples",
                "monitor_evaluation_origins",
                "epoch_train_evaluation_origins",
                "validation_batches",
                "horizons_us",
            ):
                self.assertEqual(getattr(repeated, name), getattr(baseline, name), name)

    def test_epoch_boundary_contract_uses_monitor_then_final_validation(self) -> None:
        self.assertEqual(
            [
                _epoch_boundary_evaluation_namespace(
                    epoch=epoch,
                    epochs=4,
                    full_validation_final_epoch_only=True,
                )
                for epoch in range(1, 5)
            ],
            ["monitor", "monitor", "monitor", "validation"],
        )
        self.assertEqual(
            _epoch_boundary_evaluation_namespace(
                epoch=1, epochs=4, full_validation_final_epoch_only=False
            ),
            "validation",
        )

    def test_nested_subset_is_deterministic_complete_and_distribution_bounded(self) -> None:
        parent = tuple(_row(index) for index in range(8_192))
        parent_origins = sum(int(row["origins"]) for row in parent)
        with (
            patch(
                "research.bar_gpt.v2.run_train_repetition_comparison.REPETITION_TRAIN_ORIGINS",
                parent_origins // 4,
            ),
            patch(
                "research.bar_gpt.v2.run_train_repetition_comparison.SELECTION_CANDIDATE_SALTS",
                16,
            ),
            patch(
                "research.bar_gpt.v2.run_train_repetition_comparison.MAX_ABSOLUTE_SHARE_DRIFT",
                0.05,
            ),
        ):
            first, first_salt, first_audit = select_repetition_subset(parent)
            second, second_salt, second_audit = select_repetition_subset(parent)
        self.assertEqual(first, second)
        self.assertEqual(first_salt, second_salt)
        self.assertEqual(first_audit, second_audit)
        self.assertEqual(first_audit["missing_dimension_values"], 0)
        self.assertLessEqual(first_audit["max_absolute_share_drift"], 0.05)
        self.assertLessEqual(
            abs(sum(int(row["origins"]) for row in first) * 4 - parent_origins),
            max(int(row["origins"]) for row in parent) * 4,
        )
        parent_identities = {
            (row["unit_key"], row["session_index"], row["block_index"])
            for row in parent
        }
        self.assertTrue(
            {
                (row["unit_key"], row["session_index"], row["block_index"])
                for row in first
            }
            <= parent_identities
        )
        self.assertEqual(
            _distribution_audit(parent, first)["missing_dimension_values"], 0
        )

    def test_rerun_resumes_only_its_repetition_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            checkpoint = (
                output_root
                / "runs"
                / repetition_run_name("current", "fixed")
                / "checkpoints"
                / "checkpoint_latest.pt"
            )
            checkpoint.parent.mkdir(parents=True)
            checkpoint.touch()
            argv = trainer_argv(
                "current",
                run_stamp="fixed",
                wandb_mode="disabled",
                output_root=output_root,
            )
        self.assertEqual(argv[argv.index("--resume-checkpoint") + 1], str(checkpoint))

    def test_all_repetition_runs_use_fresh_processes_sequentially(self) -> None:
        completed = SimpleNamespace(returncode=0)
        with patch(
            "research.bar_gpt.v2.run_train_repetition_comparison.subprocess.run",
            side_effect=(completed, completed, completed),
        ) as run:
            with redirect_stdout(io.StringIO()):
                exit_code = main(
                    (
                        "--model-size",
                        "all",
                        "--run-stamp",
                        "shared",
                        "--wandb-mode",
                        "disabled",
                        "--execute",
                    )
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_count, 3)
        for call, model_size in zip(run.call_args_list, COMPARISON_RUNS, strict=True):
            command = call.args[0]
            self.assertEqual(command[command.index("--model-size") + 1], model_size)
            self.assertIn("--execute", command)
            self.assertEqual(call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_launcher_command_is_separate_from_original_comparison(self) -> None:
        command = _launcher_command(
            "current",
            run_stamp="fixed",
            wandb_mode="disabled",
            execute=True,
            shard_root=Path("shards"),
            output_root=Path("runtime"),
        )
        self.assertIn("research.bar_gpt.v2.run_train_repetition_comparison", command)
        self.assertNotIn("research.bar_gpt.v2.run_train_model_comparison", command)


if __name__ == "__main__":
    unittest.main()

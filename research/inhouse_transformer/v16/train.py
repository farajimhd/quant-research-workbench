from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import Any, Iterable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.v16.config import (  # noqa: E402
    DataConfig,
    ExperimentConfig,
    ModelConfig,
    TrainConfig,
)
from research.inhouse_transformer.v16.data import (  # noqa: E402
    BatchBuilder,
    RollingBarWindowDataset,
    available_sessions,
    binary_magnitude_logits_to_distribution_stats,
    count_coverage,
    iter_ticker_frames,
    load_session_frame,
    parse_ticker_list,
    resolve_end_date,
    select_top_tickers,
    target_bit_count,
    ticker_arrays,
    target_values_to_bps,
    valid_origins,
)
from research.inhouse_transformer.v16.metrics import MetricAccumulator, append_jsonl  # noqa: E402
from research.inhouse_transformer.model_artifacts import save_model_architecture_artifacts  # noqa: E402

torch = None
DataLoader = None
FeatureTemporalTransformer = None
forecast_loss = None
LOG_RULE = "*" * 96
EXPERIMENT_VERSION = "v16"
MODEL_ARCHITECTURE_NAME = "concat_market_time_tokens"
DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE = 1024
DEFAULT_OVERFIT_WINDOW_COUNT = 8192
METRIC_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "train_step": {
        "description": "Optimizer step used as the shared x-axis for all W&B metrics.",
        "unit": "step",
        "interpretation": "Monotonic increase means training is progressing.",
    },
    "loss": {
        "description": "Total objective used for the current split or progress row.",
        "unit": "target units",
        "interpretation": "Down is better. For v16 this is binary cross entropy over sign plus magnitude bits.",
    },
    "regression_loss": {
        "description": "Binary cross entropy between encoded target logits and target bits.",
        "unit": "target units",
        "interpretation": "Down is better.",
    },
    "bit_accuracy_pct": {
        "description": "Share of encoded target bits whose sigmoid probability is on the correct side of 0.5.",
        "unit": "percent",
        "interpretation": "Up is better. It is an encoding diagnostic, not decoded price accuracy.",
    },
    "lr": {
        "description": "Current learning rate from the optimizer.",
        "unit": "learning rate",
        "interpretation": "Use with loss curves to understand scheduler effects.",
    },
    "epoch": {
        "description": "Estimated epoch. For overfit cache runs this is cycles through the cached batches.",
        "unit": "epoch",
        "interpretation": "Up means more passes over the configured training source.",
    },
    "samples_per_sec": {
        "description": "Training throughput measured over the latest logging interval.",
        "unit": "windows/sec",
        "interpretation": "Up means faster training; sudden drops can indicate eval, IO, or GPU stalls.",
    },
    "windows": {
        "description": "Number of prediction windows included in the metric row.",
        "unit": "windows",
        "interpretation": "Higher means the metric is based on more samples.",
    },
    "batches": {
        "description": "Number of batches included in the metric row.",
        "unit": "batches",
        "interpretation": "Higher means the metric is based on more batches.",
    },
    "windows_per_sec": {
        "description": "Evaluation throughput for validation/test progress rows.",
        "unit": "windows/sec",
        "interpretation": "Up means faster evaluation.",
    },
    "h1_mae_bps": {
        "description": "Horizon-1 close mean absolute forecast error, converted to basis points versus current close.",
        "unit": "bps",
        "interpretation": "Down is better.",
    },
    "h1_hard_decoded_mae_bps": {
        "description": "Horizon-1 close MAE using the hard 0.5-threshold binary magnitude decode.",
        "unit": "bps",
        "interpretation": "Down is better. This is the legacy v16 decoded prediction error.",
    },
    "h1_expected_signed_mae_bps": {
        "description": "Horizon-1 close MAE using probability-weighted expected signed bps from the binary bits.",
        "unit": "bps",
        "interpretation": "Down is better. Compare against hard_decoded_mae_bps.",
    },
    "h1_expected_signed_rmse_bps": {
        "description": "RMSE for probability-weighted expected signed bps.",
        "unit": "bps",
        "interpretation": "Down is better and penalizes large expected-value misses.",
    },
    "h1_expected_signed_corr": {
        "description": "Correlation between expected signed bps and actual horizon-1 close bps.",
        "unit": "correlation",
        "interpretation": "Higher is better; near zero means little linear relationship.",
    },
    "h1_expected_dir_acc_pct": {
        "description": "Direction accuracy using expected signed bps rather than hard-decoded bps.",
        "unit": "percent",
        "interpretation": "Up is better.",
    },
    "h1_final_mae_bps": {
        "description": "Alias for horizon-1 close MAE using the probability-weighted final prediction.",
        "unit": "bps",
        "interpretation": "Down is better. This is the main final-prediction error metric for binary target runs.",
    },
    "h1_final_dir_acc_pct": {
        "description": "Alias for direction accuracy of the probability-weighted final prediction.",
        "unit": "percent",
        "interpretation": "Up is better. This is the main final-prediction direction metric for binary target runs.",
    },
    "h1_final_corr": {
        "description": "Alias for correlation between final predicted move and actual move.",
        "unit": "correlation",
        "interpretation": "Higher is better; near zero means little linear relationship.",
    },
    "h1_final_edge_vs_last_move_naive_bps": {
        "description": "Last-move continuation MAE minus final-prediction MAE for horizon-1 close.",
        "unit": "bps",
        "interpretation": "Positive is better; negative means copying the last move beat the final prediction.",
    },
    "h1_expected_edge_vs_last_move_naive_bps": {
        "description": "Same as h1_final_edge_vs_last_move_naive_bps; kept with expected-signed naming for consistency.",
        "unit": "bps",
        "interpretation": "Positive is better; negative means copying the last move beat the expected signed prediction.",
    },
    "h1_model_behavior_score": {
        "description": "Bounded 0..100 composite of final direction accuracy, final correlation, and final edge versus last-move continuation.",
        "unit": "score",
        "interpretation": "Higher is better. Use as a quick overview, then inspect the underlying final MAE, direction, correlation, and edge metrics.",
    },
    "h1_mean_confidence": {
        "description": "Mean bounded value confidence abs(expected_signed_bps)/(abs(expected_signed_bps)+magnitude_std_bps).",
        "unit": "0..1",
        "interpretation": "Higher means the model's expected move is large relative to weighted bit uncertainty.",
    },
    "h1_mean_magnitude_std_bps": {
        "description": "Mean weighted magnitude uncertainty implied by binary bit probabilities.",
        "unit": "bps",
        "interpretation": "Lower means the magnitude bits imply a tighter bps distribution.",
    },
    "h1_mean_p_up": {
        "description": "Mean sign-bit probability of an up move.",
        "unit": "probability",
        "interpretation": "Values near 0.5 mean neutral aggregate directional bias.",
    },
    "h1_mean_sign_confidence": {
        "description": "Mean absolute sign certainty abs(2*p_up-1).",
        "unit": "0..1",
        "interpretation": "Higher means the sign bit is farther from undecided.",
    },
    "h1_coverage_at_conf_0_5_pct": {
        "description": "Share of horizon-1 close predictions with confidence >= 0.5.",
        "unit": "percent",
        "interpretation": "Higher means more predictions pass the confidence filter.",
    },
    "h1_mae_at_conf_0_5_bps": {
        "description": "Expected signed bps MAE among horizon-1 close predictions with confidence >= 0.5.",
        "unit": "bps",
        "interpretation": "Down is better. Use with coverage_at_conf_0_5_pct.",
    },
    "h1_dir_acc_at_conf_0_5_pct": {
        "description": "Expected-direction accuracy among horizon-1 close predictions with confidence >= 0.5.",
        "unit": "percent",
        "interpretation": "Up is better. Useful only if coverage is non-trivial.",
    },
    "h1_rmse_bps": {
        "description": "Horizon-1 close root mean squared forecast error in basis points.",
        "unit": "bps",
        "interpretation": "Down is better and penalizes large misses more than MAE.",
    },
    "h1_dir": {
        "description": "Horizon-1 close direction accuracy. Direction is predicted move sign versus actual move sign from current close.",
        "unit": "percent",
        "interpretation": "Up is better. Compare against last-move and mean-reversion direction baselines before trusting it.",
    },
    "h1_dir_acc_pct": {
        "description": "Same as h1_dir, kept as an explicit percent alias.",
        "unit": "percent",
        "interpretation": "Up is better.",
    },
    "h1_edge_bps": {
        "description": "Persistence baseline MAE minus model MAE for horizon-1 close.",
        "unit": "bps",
        "interpretation": "Positive is better; negative means persistence beat the model.",
    },
    "h1_naive_mae_bps": {
        "description": "Persistence baseline MAE where predicted return is zero and predicted close equals current close.",
        "unit": "bps",
        "interpretation": "Baseline reference. Lower means the evaluated samples were easier for persistence.",
    },
    "h1_last_move_naive_mae_bps": {
        "description": "Last-move continuation baseline MAE for horizon-1 close.",
        "unit": "bps",
        "interpretation": "Baseline reference. Compare model MAE and edge_vs_last_move against this.",
    },
    "h1_edge_vs_last_move_naive_bps": {
        "description": "Last-move continuation MAE minus model MAE for horizon-1 close.",
        "unit": "bps",
        "interpretation": "Positive is better; negative means copying the last move beat the model.",
    },
    "h1_last_move_dir_acc_pct": {
        "description": "Direction accuracy of the last-move continuation baseline.",
        "unit": "percent",
        "interpretation": "Baseline reference for h1_dir. The model should beat this on validation/test.",
    },
    "h1_mean_reversion_naive_mae_bps": {
        "description": "Mean-reversion baseline MAE using the opposite of the last close return.",
        "unit": "bps",
        "interpretation": "Baseline reference. Lower means mean reversion fits the evaluated samples better.",
    },
    "h1_edge_vs_mean_reversion_naive_bps": {
        "description": "Mean-reversion baseline MAE minus model MAE for horizon-1 close.",
        "unit": "bps",
        "interpretation": "Positive is better; negative means mean reversion beat the model.",
    },
    "h1_mean_reversion_dir_acc_pct": {
        "description": "Direction accuracy of the mean-reversion baseline.",
        "unit": "percent",
        "interpretation": "Baseline reference for h1_dir.",
    },
    "h1_corr": {
        "description": "Correlation between predicted and actual horizon-1 close moves in bps.",
        "unit": "correlation",
        "interpretation": "Higher is better; near zero means little linear relationship.",
    },
}
METRIC_PRIORITY_ORDER = (
    "loss",
    "bit_accuracy_pct",
    "h1_model_behavior_score",
    "h1_final_mae_bps",
    "h1_final_dir_acc_pct",
    "h1_final_corr",
    "h1_final_edge_vs_last_move_naive_bps",
    "h1_expected_signed_mae_bps",
    "h1_expected_signed_corr",
    "h1_expected_dir_acc_pct",
    "h1_expected_edge_vs_last_move_naive_bps",
    "h1_mean_confidence",
    "h1_mean_magnitude_std_bps",
    "h1_coverage_at_conf_0_5_pct",
    "h1_mae_at_conf_0_5_bps",
    "h1_dir_acc_at_conf_0_5_pct",
    "h1_coverage_at_conf_0_7_pct",
    "h1_mae_at_conf_0_7_bps",
    "h1_dir_acc_at_conf_0_7_pct",
    "h1_hard_decoded_mae_bps",
    "h1_dir_acc_pct",
    "h1_corr",
    "h1_edge_vs_last_move_naive_bps",
    "h1_last_move_dir_acc_pct",
    "h1_naive_mae_bps",
    "h1_last_move_naive_mae_bps",
    "h1_mean_p_up",
    "h1_mean_sign_confidence",
    "h1_rmse_bps",
    "h1_edge_bps",
    "h1_mean_reversion_naive_mae_bps",
    "h1_edge_vs_mean_reversion_naive_bps",
    "h1_mean_reversion_dir_acc_pct",
    "windows",
    "batches",
    "lr",
    "epoch",
    "samples_per_sec",
    "windows_per_sec",
    "regression_loss",
)
METRIC_PRIORITY_INDEX = {name: index + 1 for index, name in enumerate(METRIC_PRIORITY_ORDER)}
PRIORITY_WANDB_ALIASES = (
    ("loss", "loss"),
    ("bit_accuracy_pct", "bit_accuracy_pct"),
    ("h1_model_behavior_score", "h1_close_model_behavior_score"),
    ("h1_final_mae_bps", "h1_close_expected_signed_mae_bps"),
    ("h1_final_dir_acc_pct", "h1_close_expected_dir_acc_pct"),
    ("h1_final_corr", "h1_close_expected_signed_corr"),
    ("h1_final_edge_vs_last_move_naive_bps", "h1_close_expected_edge_vs_last_move_naive_bps"),
    ("h1_mean_confidence", "h1_close_mean_confidence"),
    ("h1_mean_magnitude_std_bps", "h1_close_mean_magnitude_std_bps"),
    ("h1_coverage_at_conf_0_5_pct", "h1_close_coverage_at_conf_0_5_pct"),
    ("h1_mae_at_conf_0_5_bps", "h1_close_mae_at_conf_0_5_bps"),
    ("h1_dir_acc_at_conf_0_5_pct", "h1_close_dir_acc_at_conf_0_5_pct"),
    ("h1_coverage_at_conf_0_7_pct", "h1_close_coverage_at_conf_0_7_pct"),
    ("h1_mae_at_conf_0_7_bps", "h1_close_mae_at_conf_0_7_bps"),
    ("h1_dir_acc_at_conf_0_7_pct", "h1_close_dir_acc_at_conf_0_7_pct"),
    ("h1_hard_decoded_mae_bps", "h1_close_hard_decoded_mae_bps"),
    ("h1_hard_dir_acc_pct", "h1_close_dir_acc_pct"),
    ("h1_hard_corr", "h1_close_corr"),
    ("h1_edge_vs_last_move_naive_bps", "h1_close_edge_vs_last_move_naive_bps"),
    ("h1_last_move_dir_acc_pct", "h1_close_last_move_dir_acc_pct"),
)
CORE_WANDB_DIRECT_KEYS = {
    "loss",
    "bit_accuracy_pct",
    "lr",
    "samples_per_sec",
    "epoch",
    "eval_batches",
}
CONFIDENCE_BUCKET_RANGES = tuple((i * 10, (i + 1) * 10) for i in range(10))


def attach_wandb_metric_metadata(wandb_run: Any, wandb_module: Any) -> None:
    rows = [
        [
            metric_priority(name),
            name,
            meta["description"],
            meta["unit"],
            meta["interpretation"],
        ]
        for name, meta in sorted(METRIC_DESCRIPTIONS.items(), key=lambda item: (metric_priority(item[0]), item[0]))
    ]
    try:
        wandb_run.config.update({"metric_descriptions": METRIC_DESCRIPTIONS}, allow_val_change=True)
    except Exception as exc:
        print(f"*** W&B metric description config skipped: {exc}", flush=True)
    try:
        wandb_run.summary["metric_descriptions"] = METRIC_DESCRIPTIONS
    except Exception as exc:
        print(f"*** W&B metric description summary skipped: {exc}", flush=True)
    try:
        table = wandb_module.Table(
            columns=["priority", "metric", "description", "unit", "interpretation"],
            data=rows,
        )
        wandb_run.log({"metric_descriptions/table": table, "train_step": 0})
    except Exception as exc:
        print(f"*** W&B metric description table skipped: {exc}", flush=True)


def metric_priority(name: str) -> int:
    return METRIC_PRIORITY_INDEX.get(name, 999)


class NonFiniteLossError(FloatingPointError):
    def __init__(self, *, label: str, step: int, details: str) -> None:
        self.label = label
        self.step = step
        self.details = details
        super().__init__(f"Non-finite {label} loss at step {step:,}. {details}")


def parse_args() -> argparse.Namespace:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(
        description=(
            "Train an in-house feature/time transformer baseline on provider-built 1m bars. "
            "Defaults use train sessions through 2025, validation in Jan-Feb 2026, and test after Mar 2026."
        )
    )
    parser.add_argument("--processed-root", default=str(defaults.data.processed_root))
    parser.add_argument("--train-start-date", default=defaults.data.train_start_date)
    parser.add_argument("--train-end-date", default=defaults.data.train_end_date)
    parser.add_argument("--validation-start-date", default=defaults.data.validation_start_date)
    parser.add_argument("--validation-end-date", default=defaults.data.validation_end_date)
    parser.add_argument("--test-start-date", default=defaults.data.test_start_date)
    parser.add_argument("--test-end-date", default=defaults.data.test_end_date)
    parser.add_argument("--session-scope", choices=["all", "regular"], default=defaults.data.session_scope)
    parser.add_argument("--context-length", type=int, default=defaults.data.context_length)
    parser.add_argument("--horizon", type=int, default=defaults.data.horizon)
    parser.add_argument(
        "--target-mode",
        choices=["binary_magnitude_bps"],
        default=defaults.data.target_mode,
        help=(
            "Main transformer target format. v16 uses sign plus 12 magnitude bits for log-return bps."
        ),
    )
    parser.add_argument(
        "--target-columns",
        default=",".join(defaults.data.target_columns),
        help="Comma-separated target columns. Use close for a one-output overfit test.",
    )
    parser.add_argument(
        "--input-normalization",
        choices=["window_zscore_only"],
        default=defaults.data.input_normalization,
        help="Input normalization for raw actual source columns. window_zscore_only applies only causal per-window z-score.",
    )
    parser.add_argument("--tickers", default="", help="Comma-separated ticker override. If set, --max-tickers is ignored.")
    parser.add_argument("--max-tickers", type=int, default=defaults.data.max_tickers)
    parser.add_argument("--allow-target-across-session", action="store_true")
    parser.add_argument("--no-carry-context-across-session", action="store_true")

    parser.add_argument("--d-model", type=int, default=defaults.model.d_model)
    parser.add_argument("--feature-attention-layers", type=int, default=defaults.model.feature_attention_layers)
    parser.add_argument(
        "--feature-attention-chunk-size",
        type=int,
        default=defaults.model.feature_attention_chunk_size,
        help="Maximum flattened batch*context rows per feature-attention call. Keeps CUDA efficient attention under kernel limits.",
    )
    parser.add_argument("--temporal-layers", type=int, default=defaults.model.temporal_layers)
    parser.add_argument("--num-heads", type=int, default=defaults.model.num_heads)
    parser.add_argument("--ff-dim", type=int, default=defaults.model.ff_dim)
    parser.add_argument("--dropout", type=float, default=defaults.model.dropout)
    parser.add_argument("--direction-threshold-bps", type=float, default=defaults.model.direction_threshold_bps)

    parser.add_argument("--batch-size", type=int, default=defaults.train.batch_size)
    parser.add_argument("--epochs", type=int, default=defaults.train.epochs)
    parser.add_argument("--max-steps", type=int, default=defaults.train.max_steps)
    parser.add_argument("--learning-rate", type=float, default=defaults.train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=defaults.train.weight_decay)
    parser.add_argument("--warmup-steps", type=int, default=defaults.train.warmup_steps)
    parser.add_argument(
        "--lr-scheduler",
        choices=["auto", "plateau", "cosine", "cosine_warm_restarts", "constant"],
        default=defaults.train.lr_scheduler,
        help=(
            "Learning-rate schedule. auto uses cosine_warm_restarts for overfit runs and plateau otherwise. "
            "plateau reduces LR on validation-loss stagnation."
        ),
    )
    parser.add_argument("--lr-plateau-factor", type=float, default=defaults.train.lr_plateau_factor)
    parser.add_argument("--lr-plateau-patience", type=int, default=defaults.train.lr_plateau_patience)
    parser.add_argument("--lr-plateau-threshold", type=float, default=defaults.train.lr_plateau_threshold)
    parser.add_argument(
        "--cosine-restart-t0-steps",
        type=int,
        default=defaults.train.cosine_restart_t0_steps,
        help="T_0 for CosineAnnealingWarmRestarts in optimizer steps. 0 chooses a run-aware default.",
    )
    parser.add_argument(
        "--cosine-restart-t-mult",
        type=int,
        default=defaults.train.cosine_restart_t_mult,
        help="T_mult for CosineAnnealingWarmRestarts.",
    )
    parser.add_argument("--min-learning-rate", type=float, default=defaults.train.min_learning_rate)
    parser.add_argument("--grad-clip-norm", type=float, default=defaults.train.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=defaults.train.logging_steps)
    parser.add_argument("--eval-steps", type=int, default=defaults.train.eval_steps)
    parser.add_argument(
        "--eval-progress-batches",
        type=int,
        default=defaults.train.eval_progress_batches,
        help="During validation/test, print and wandb-log partial metrics every N eval batches. 0 disables progress logs.",
    )
    parser.add_argument("--validation-window-count", type=int, default=defaults.train.validation_window_count)
    parser.add_argument("--test-window-count", type=int, default=defaults.train.test_window_count)
    parser.add_argument(
        "--max-batches-per-session",
        type=int,
        default=defaults.train.max_batches_per_session,
        help="Optional cap for quick experiments. 0 means use all eligible windows.",
    )
    parser.add_argument(
        "--count-coverage",
        action="store_true",
        help="Pre-scan all train sessions to count windows and batches. Disabled by default to reduce RAM pressure.",
    )
    parser.add_argument("--num-workers", type=int, default=defaults.train.num_workers)
    parser.add_argument("--seed", type=int, default=defaults.train.seed)
    parser.set_defaults(amp=defaults.train.amp)
    parser.add_argument("--amp", dest="amp", action="store_true")
    parser.add_argument("--no-amp", dest="amp", action="store_false")
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--output-name", default=defaults.train.output_name)
    parser.add_argument("--resume-latest", action="store_true")
    parser.add_argument(
        "--overfit-session",
        default="",
        help="Use exactly this session for train/validation/test and cache fixed train batches for an overfit sanity run.",
    )
    parser.add_argument(
        "--overfit-batches",
        type=int,
        default=0,
        help=(
            "Deprecated compatibility option. It is converted to a fixed window count using reference batch size "
            f"{DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE}, so changing --batch-size does not change cache size."
        ),
    )
    parser.add_argument(
        "--overfit-window-count",
        type=int,
        default=0,
        help=(
            "Fixed number of train windows to cache for overfit tests. "
            f"Defaults to {DEFAULT_OVERFIT_WINDOW_COUNT} when --overfit-session is set."
        ),
    )
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-project", default="May2026-1m-timeseries-v14-variants")
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--disable-wandb", action="store_true")
    parser.add_argument("--device", default="cuda", help='Use "cuda" when available, otherwise "cpu".')
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    processed_root = Path(args.processed_root)
    data = DataConfig(
        processed_root=processed_root,
        train_start_date=args.train_start_date,
        train_end_date=args.train_end_date,
        validation_start_date=args.validation_start_date,
        validation_end_date=args.validation_end_date,
        test_start_date=args.test_start_date,
        test_end_date=resolve_end_date(processed_root, args.test_end_date),
        session_scope=args.session_scope,
        context_length=args.context_length,
        horizon=args.horizon,
        target_mode=args.target_mode,
        target_columns=parse_column_list(args.target_columns),
        input_normalization=args.input_normalization,
        tickers=parse_ticker_list(args.tickers),
        max_tickers=args.max_tickers,
        allow_target_across_session=bool(args.allow_target_across_session),
        carry_context_across_session=not bool(args.no_carry_context_across_session),
    )
    model = ModelConfig(
        d_model=args.d_model,
        feature_attention_layers=args.feature_attention_layers,
        feature_attention_chunk_size=args.feature_attention_chunk_size,
        temporal_layers=args.temporal_layers,
        num_heads=args.num_heads,
        ff_dim=args.ff_dim,
        dropout=args.dropout,
        target_bit_count=target_bit_count(data),
        direction_threshold_bps=args.direction_threshold_bps,
    )
    lr_scheduler = resolve_lr_scheduler(args)
    train = TrainConfig(
        batch_size=args.batch_size,
        epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        warmup_steps=args.warmup_steps,
        lr_scheduler=lr_scheduler,
        lr_plateau_factor=args.lr_plateau_factor,
        lr_plateau_patience=args.lr_plateau_patience,
        lr_plateau_threshold=args.lr_plateau_threshold,
        cosine_restart_t0_steps=args.cosine_restart_t0_steps,
        cosine_restart_t_mult=args.cosine_restart_t_mult,
        min_learning_rate=args.min_learning_rate,
        grad_clip_norm=args.grad_clip_norm,
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        eval_progress_batches=args.eval_progress_batches,
        validation_window_count=args.validation_window_count,
        test_window_count=args.test_window_count,
        max_batches_per_session=args.max_batches_per_session,
        count_coverage=args.count_coverage,
        num_workers=args.num_workers,
        seed=args.seed,
        amp=args.amp,
        compile_model=args.compile_model,
        output_name=args.output_name,
        resume_latest=args.resume_latest,
    )
    return ExperimentConfig(data=data, model=model, train=train)


def resolve_lr_scheduler(args: argparse.Namespace) -> str:
    if args.lr_scheduler != "auto":
        return args.lr_scheduler
    if args.overfit_session or args.overfit_batches > 0 or args.overfit_window_count > 0:
        return "cosine_warm_restarts"
    return "plateau"


def resolve_overfit_window_count(args: argparse.Namespace) -> int:
    if args.overfit_window_count < 0:
        raise SystemExit("--overfit-window-count must be >= 0.")
    if args.overfit_batches < 0:
        raise SystemExit("--overfit-batches must be >= 0.")
    if args.overfit_window_count > 0:
        return int(args.overfit_window_count)
    if args.overfit_batches > 0:
        return int(args.overfit_batches) * DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE
    if args.overfit_session:
        return DEFAULT_OVERFIT_WINDOW_COUNT
    return 0


def parse_column_list(raw: str) -> tuple[str, ...]:
    columns = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    allowed = {"open", "high", "low", "close"}
    invalid = sorted(set(columns) - allowed)
    if invalid:
        raise SystemExit(f"Unsupported target columns: {invalid}. Allowed columns: {sorted(allowed)}")
    if "close" not in columns:
        raise SystemExit("Target columns must include close so direction and naive metrics can be computed.")
    return columns


def make_wandb_run_name(args: argparse.Namespace, config: ExperimentConfig) -> str:
    if args.wandb_run_name:
        return versioned_wandb_run_name(args.wandb_run_name)
    target_columns = "-".join(config.data.target_columns)
    input_name = input_experiment_name(config)
    if args.overfit_session:
        return (
            f"{EXPERIMENT_VERSION}-main-transformer-overfit-{args.overfit_session}-{input_name}-"
            f"{config.data.target_mode}-ctx{config.data.context_length}-h{config.data.horizon}-{target_columns}"
        )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        f"{EXPERIMENT_VERSION}-main-transformer-{input_name}-{config.data.target_mode}-ctx{config.data.context_length}-"
        f"h{config.data.horizon}-{target_columns}-{timestamp}"
    )


def versioned_wandb_run_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if not stripped:
        return stripped
    if stripped.startswith(f"{EXPERIMENT_VERSION}-"):
        return stripped
    version_prefix = re.match(r"^v\d+[-_](.+)$", stripped)
    if version_prefix:
        return f"{EXPERIMENT_VERSION}-{version_prefix.group(1)}"
    return f"{EXPERIMENT_VERSION}-{stripped}"


def input_experiment_name(config: ExperimentConfig) -> str:
    if config.data.input_normalization == "window_zscore_only":
        return "expanded-time-conditioning-window-zscore"
    return config.data.input_normalization.replace("_", "-")


def read_env_key(env_path: Path, name: str) -> str:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        return value.strip().strip("\"'")
    return ""


def resolve_wandb_api_key() -> str:
    api_key = os.environ.get("WANDB_API_KEY", "").strip()
    if api_key:
        return api_key

    env_paths = []
    for env_path in (REPO_ROOT / ".env", Path.cwd() / ".env"):
        if env_path not in env_paths:
            env_paths.append(env_path)

    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        load_dotenv = None

    for env_path in env_paths:
        if not env_path.exists():
            continue
        if load_dotenv is not None:
            load_dotenv(env_path, override=False)
        else:
            dotenv_api_key = read_env_key(env_path, "WANDB_API_KEY")
            if dotenv_api_key:
                os.environ.setdefault("WANDB_API_KEY", dotenv_api_key)
        api_key = os.environ.get("WANDB_API_KEY", "").strip()
        if api_key:
            print(f"*** WANDB_API_KEY loaded from {env_path}", flush=True)
            return api_key
    return ""


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, metadata: dict[str, Any]) -> Any:
    if args.disable_wandb:
        print("*** WANDB disabled by --disable-wandb", flush=True)
        return None
    try:
        import wandb
    except ModuleNotFoundError:
        print("*** WANDB package is not installed; metrics will only be written to metrics.jsonl.", flush=True)
        return None
    run_name = make_wandb_run_name(args, config)
    print(f"*** WANDB INIT | entity={args.wandb_entity} | project={args.wandb_project} | run={run_name}", flush=True)
    api_key = resolve_wandb_api_key()
    if not api_key:
        print(
            f"*** WANDB_API_KEY is not set in this process environment or {REPO_ROOT / '.env'}; "
            "metrics will only be written to metrics.jsonl.",
            flush=True,
        )
        return None
    try:
        wandb.login(key=api_key, relogin=True)
    except Exception as exc:
        print(
            "*** WANDB login failed using WANDB_API_KEY; "
            f"metrics will only be written to metrics.jsonl. Error: {exc}",
            flush=True,
        )
        return None
    try:
        run = wandb.init(
            entity=args.wandb_entity,
            project=args.wandb_project,
            name=run_name,
            config=metadata,
        )
        print(f"*** WANDB RUN READY | url={getattr(run, 'url', '')}", flush=True)
        try:
            run.define_metric("train_step")
            run.define_metric("*", step_metric="train_step")
        except Exception as exc:
            print(f"*** WANDB metric axis setup skipped: {exc}", flush=True)
        attach_wandb_metric_metadata(run, wandb)
        run.log({"run/started": 1, "train_step": 0})
        return run
    except Exception as exc:
        print(f"*** WANDB init failed; metrics will only be written to metrics.jsonl. Error: {exc}", flush=True)
        return None


def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    set_seed(config.train.seed)
    train_sessions = available_sessions(
        config.data.processed_root, config.data.train_start_date, config.data.train_end_date
    )
    validation_sessions = available_sessions(
        config.data.processed_root, config.data.validation_start_date, config.data.validation_end_date
    )
    test_sessions = available_sessions(
        config.data.processed_root, config.data.test_start_date, config.data.test_end_date
    )
    overfit_window_count = resolve_overfit_window_count(args)
    if args.overfit_session:
        requested_session = args.overfit_session
        all_train_sessions = set(train_sessions)
        if requested_session not in all_train_sessions:
            raise SystemExit(
                f"--overfit-session {requested_session} is not inside the selected train split "
                f"{config.data.train_start_date} -> {config.data.train_end_date}."
            )
        train_sessions = [requested_session]
        validation_sessions = [requested_session]
        test_sessions = [requested_session]
    tickers = config.data.tickers or select_top_tickers(
        config.data.processed_root, train_sessions, config.data.max_tickers
    )

    output_dir = make_output_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.jsonl"
    wandb_run_name = make_wandb_run_name(args, config)
    if args.wandb_run_name and wandb_run_name != args.wandb_run_name:
        print(
            f"*** WANDB RUN NAME NORMALIZED | requested={args.wandb_run_name} | using={wandb_run_name}",
            flush=True,
        )
    metadata = metadata_payload(config, train_sessions, validation_sessions, test_sessions, tickers, output_dir)
    metadata["runtime"] = {
        "overfit_session": args.overfit_session,
        "overfit_window_count": overfit_window_count,
        "overfit_batches_requested": args.overfit_batches,
        "overfit_reference_batch_size": DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE,
        "wandb_project": args.wandb_project,
        "wandb_entity": args.wandb_entity,
        "wandb_run_name": wandb_run_name,
        "wandb_disabled": bool(args.disable_wandb),
    }
    write_json(output_dir / "metadata.json", metadata)

    print_split_summary(metadata)
    print(
        f"Features={len(config.data.input_feature_columns)} time_features={len(config.data.time_feature_columns)} "
        f"targets={list(config.data.target_columns)} horizon={config.data.horizon}",
        flush=True,
    )
    print(
        f"Model architecture: {EXPERIMENT_VERSION} {MODEL_ARCHITECTURE_NAME} "
        f"(market_tokens={len(config.data.input_feature_columns)} "
        f"time_tokens={len(config.data.time_feature_columns)} "
        f"total_feature_attention_tokens={len(config.data.input_feature_columns) + len(config.data.time_feature_columns)})",
        flush=True,
    )
    print(f"Target mode: {config.data.target_mode}", flush=True)
    print(f"Input normalization: {config.data.input_normalization}", flush=True)
    print(
        f"LR scheduler: {config.train.lr_scheduler}"
        + (
            f" t0_steps={cosine_restart_t0_steps(config.train)} t_mult={config.train.cosine_restart_t_mult}"
            if config.train.lr_scheduler == "cosine_warm_restarts"
            else ""
        ),
        flush=True,
    )
    print(f"Output directory: {output_dir}", flush=True)

    coverage = None
    if config.train.count_coverage:
        coverage = count_coverage(
            config=config.data,
            sessions=train_sessions,
            tickers=tickers,
            batch_size=config.train.batch_size,
            max_batches_per_session=config.train.max_batches_per_session,
        )
    planned_steps = config.train.max_steps
    if overfit_window_count > 0 and planned_steps <= 0:
        planned_steps = config.train.epochs * math.ceil(overfit_window_count / config.train.batch_size)
    print_training_plan(config, coverage, planned_steps)
    if args.dry_run:
        print("Dry run complete after data split, ticker selection, and optional coverage count.", flush=True)
        return

    load_torch_stack()
    wandb_run = init_wandb(args, config, metadata)
    set_seed(config.train.seed)
    device = resolve_device(args.device)
    model = FeatureTemporalTransformer(
        feature_count=len(config.data.input_feature_columns),
        time_feature_count=len(config.data.time_feature_columns),
        context_length=config.data.context_length,
        horizon=config.data.horizon,
        target_count=len(config.data.target_columns),
        config=config.model,
    ).to(device)
    architecture_info = save_model_architecture_artifacts(
        model=model,
        data_config=config.data,
        output_dir=output_dir,
        version=EXPERIMENT_VERSION,
        torch_module=torch,
        wandb_run=wandb_run,
    )
    metadata["model_architecture"] = architecture_info
    write_json(output_dir / "metadata.json", metadata)
    print(f"*** Model architecture artifacts: {output_dir / 'model_architecture'}", flush=True)
    if config.train.compile_model:
        model = torch.compile(model)

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.train.learning_rate,
        weight_decay=config.train.weight_decay,
    )
    set_optimizer_base_lrs(optimizer)
    scheduler = make_scheduler(optimizer, config.train, planned_steps)
    scaler = (
        torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
        if hasattr(torch, "amp")
        else torch.cuda.amp.GradScaler(enabled=config.train.amp and device.type == "cuda")
    )
    start_step, best_score = maybe_resume(model, optimizer, scheduler, output_dir, config.train.resume_latest, device)

    train_dataset = RollingBarWindowDataset(
        config=config.data,
        sessions=train_sessions,
        tickers=tickers,
        batch_size=config.train.batch_size,
        seed=config.train.seed,
        mode="train",
        epochs=config.train.epochs,
        max_batches_per_session=config.train.max_batches_per_session,
        shuffle=True,
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=None,
        num_workers=config.train.num_workers,
        pin_memory=device.type == "cuda",
    )
    cached_batches = collect_overfit_batches(train_loader, overfit_window_count) if overfit_window_count > 0 else []
    if cached_batches:
        planned_steps = config.train.max_steps or config.train.epochs * len(cached_batches)
        train_iter: Iterable[dict[str, Any]] = cycle(cached_batches)
        cached_windows = sum(batch_window_count(batch) for batch in cached_batches)
        metadata["runtime"]["overfit_cached_windows"] = cached_windows
        metadata["runtime"]["overfit_cached_batches"] = len(cached_batches)
        write_json(output_dir / "metadata.json", metadata)
        print_section(
            f"OVERFIT CACHE READY windows={cached_windows:,} batches={len(cached_batches):,} "
            f"planned_steps={planned_steps:,}"
        )
    else:
        train_iter = train_loader

    running_loss = 0.0
    running_regression = 0.0
    running_bit_accuracy = 0.0
    running_batches = 0
    step = start_step
    last_eval_step = 0
    last_log_time = time.perf_counter()
    train_iterator = iter(train_iter)
    while True:
        if planned_steps > 0 and step >= planned_steps:
            break
        try:
            batch = next(train_iterator)
        except StopIteration:
            break
        step += 1
        apply_pre_step_lr(optimizer, config.train, step)
        model.train()
        batch = move_batch(batch, device)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=config.train.amp and device.type == "cuda"):
            prediction = model(batch["values"], batch["time_features"])
            loss, loss_parts = forecast_loss(
                prediction,
                batch["targets"],
            )
        try:
            raise_on_nonfinite_loss(loss, label="train", step=step, batch=batch)
        except NonFiniteLossError as exc:
            stop_training_for_nonfinite_loss(
                exc,
                output_dir=output_dir,
                model=model,
                optimizer=optimizer,
                scheduler=scheduler,
                step=step,
                best_score=best_score,
                config=config,
                metrics_path=metrics_path,
                wandb_run=wandb_run,
            )
            return
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
        scaler.step(optimizer)
        scaler.update()
        step_batch_scheduler(scheduler, config.train, step)

        running_loss += loss_parts["loss"]
        running_regression += loss_parts["regression_loss"]
        running_bit_accuracy += loss_parts.get("bit_accuracy_pct", math.nan)
        running_batches += 1
        epoch_value = estimate_epoch(step, cached_batches, config.train)

        if step == 1 or step % config.train.logging_steps == 0:
            elapsed = max(1e-6, time.perf_counter() - last_log_time)
            avg_loss = running_loss / max(1, running_batches)
            avg_regression = running_regression / max(1, running_batches)
            avg_bit_accuracy = running_bit_accuracy / max(1, running_batches)
            samples_per_sec = config.train.batch_size * running_batches / elapsed
            lr = optimizer.param_groups[0]["lr"]
            train_metrics = batch_metrics_from_prediction(prediction.detach(), batch, config)
            print(
                f"train step={step_text(step, planned_steps)} loss={avg_loss:.6f} "
                f"reg={avg_regression:.6f} bit_acc={avg_bit_accuracy:.2f}% "
                f"h1_mae={train_metrics.get('h1_close_mae_bps', math.nan):.3f}bps "
                f"h1_exp_mae={train_metrics.get('h1_close_expected_signed_mae_bps', math.nan):.3f}bps "
                f"h1_dir={train_metrics.get('h1_close_dir_acc_pct', math.nan):.2f}% "
                f"h1_exp_dir={train_metrics.get('h1_close_expected_dir_acc_pct', math.nan):.2f}% "
                f"conf={train_metrics.get('h1_close_mean_confidence', math.nan):.3f} "
                f"h1_edge_last={train_metrics.get('h1_close_edge_vs_last_move_naive_bps', math.nan):.3f}bps "
                f"lr={lr:.3e} samples_s={samples_per_sec:,.0f}",
                flush=True,
            )
            log_metrics(
                metrics_path,
                wandb_run,
                {
                    "type": "train",
                    "step": step,
                    "loss": avg_loss,
                    "regression_loss": avg_regression,
                    "bit_accuracy_pct": avg_bit_accuracy,
                    "lr": lr,
                    "samples_per_sec": samples_per_sec,
                    **({"epoch": epoch_value} if epoch_value is not None else {}),
                    **train_metrics,
                    "time": datetime.now().isoformat(timespec="seconds"),
                },
            )
            running_loss = running_regression = running_bit_accuracy = 0.0
            running_batches = 0
            last_log_time = time.perf_counter()

        should_eval = (config.train.eval_steps > 0 and step % config.train.eval_steps == 0) or (
            planned_steps > 0 and step == planned_steps
        )
        if should_eval:
            if cached_batches:
                print_section(f"TRAIN-CACHE EVAL START step={step:,}")
                try:
                    cache_metrics = evaluate_cached_batches(
                        model=model,
                        config=config,
                        batches=cached_batches,
                        device=device,
                        label="train_cache",
                    )
                except NonFiniteLossError as exc:
                    stop_training_for_nonfinite_loss(
                        exc,
                        output_dir=output_dir,
                        model=model,
                        optimizer=optimizer,
                        scheduler=scheduler,
                        step=step,
                        best_score=best_score,
                        config=config,
                        metrics_path=metrics_path,
                        wandb_run=wandb_run,
                    )
                    return
                cache_metrics.update(
                    {
                        "type": "train_cache",
                        "step": step,
                        **({"epoch": epoch_value} if epoch_value is not None else {}),
                        "time": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                log_metrics(metrics_path, wandb_run, cache_metrics)
                print_metric_line(cache_metrics)
                print_section(f"TRAIN-CACHE EVAL END step={step:,}")
            print_section(f"VALIDATION START step={step:,}")
            try:
                validation_metrics = evaluate(
                    model=model,
                    config=config,
                    sessions=validation_sessions,
                    tickers=tickers,
                    device=device,
                    max_windows=config.train.validation_window_count,
                    label="validation",
                    metrics_path=metrics_path,
                    wandb_run=wandb_run,
                    step=step,
                    epoch=epoch_value,
                )
            except NonFiniteLossError as exc:
                stop_training_for_nonfinite_loss(
                    exc,
                    output_dir=output_dir,
                    model=model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    step=step,
                    best_score=best_score,
                    config=config,
                    metrics_path=metrics_path,
                    wandb_run=wandb_run,
                )
                return
            validation_metrics.update(
                {
                    "type": "validation",
                    "step": step,
                    **({"epoch": epoch_value} if epoch_value is not None else {}),
                    "time": datetime.now().isoformat(timespec="seconds"),
                }
            )
            apply_validation_scheduler(scheduler, optimizer, config.train, validation_metrics, step)
            log_metrics(metrics_path, wandb_run, validation_metrics)
            print_metric_line(validation_metrics)
            score = validation_metrics.get("validation_h1_close_mae_bps", math.inf)
            if score < best_score:
                best_score = float(score)
                save_checkpoint(output_dir / "best.pt", model, optimizer, scheduler, step, best_score, config)
                print(f"*** BEST CHECKPOINT SAVED | step={step:,} | h1_close_mae_bps={best_score:.4f}", flush=True)
            save_checkpoint(output_dir / "last.pt", model, optimizer, scheduler, step, best_score, config)
            print_section(f"VALIDATION END step={step:,}")
            last_eval_step = step

    if step > 0 and last_eval_step != step:
        print_section(f"FINAL VALIDATION START step={step:,}")
        epoch_value = estimate_epoch(step, cached_batches if "cached_batches" in locals() else [], config.train)
        try:
            validation_metrics = evaluate(
                model=model,
                config=config,
                sessions=validation_sessions,
                tickers=tickers,
                device=device,
                max_windows=config.train.validation_window_count,
                label="validation",
                metrics_path=metrics_path,
                wandb_run=wandb_run,
                step=step,
                epoch=epoch_value,
            )
        except NonFiniteLossError as exc:
            stop_training_for_nonfinite_loss(
                exc,
                output_dir=output_dir,
                model=model,
                optimizer=optimizer,
                scheduler=scheduler,
                step=step,
                best_score=best_score,
                config=config,
                metrics_path=metrics_path,
                wandb_run=wandb_run,
            )
            return
        validation_metrics.update(
            {
                "type": "validation",
                "step": step,
                **({"epoch": epoch_value} if epoch_value is not None else {}),
                "time": datetime.now().isoformat(timespec="seconds"),
            }
        )
        apply_validation_scheduler(scheduler, optimizer, config.train, validation_metrics, step)
        log_metrics(metrics_path, wandb_run, validation_metrics)
        print_metric_line(validation_metrics)
        score = validation_metrics.get("validation_h1_close_mae_bps", math.inf)
        if score < best_score:
            best_score = float(score)
            save_checkpoint(output_dir / "best.pt", model, optimizer, scheduler, step, best_score, config)
            print(f"*** BEST CHECKPOINT SAVED | step={step:,} | h1_close_mae_bps={best_score:.4f}", flush=True)
        save_checkpoint(output_dir / "last.pt", model, optimizer, scheduler, step, best_score, config)
        print_section(f"FINAL VALIDATION END step={step:,}")

    print_section(f"TEST START step={step:,}")
    epoch_value = estimate_epoch(step, cached_batches if "cached_batches" in locals() else [], config.train)
    try:
        test_metrics = evaluate(
            model=model,
            config=config,
            sessions=test_sessions,
            tickers=tickers,
            device=device,
            max_windows=config.train.test_window_count,
            label="test",
            metrics_path=metrics_path,
            wandb_run=wandb_run,
            step=step,
            epoch=epoch_value,
        )
    except NonFiniteLossError as exc:
        stop_training_for_nonfinite_loss(
            exc,
            output_dir=output_dir,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            step=step,
            best_score=best_score,
            config=config,
            metrics_path=metrics_path,
            wandb_run=wandb_run,
        )
        return
    test_metrics.update(
        {
            "type": "test",
            "step": step,
            **({"epoch": epoch_value} if epoch_value is not None else {}),
            "time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    log_metrics(metrics_path, wandb_run, test_metrics)
    print_metric_line(test_metrics)
    save_checkpoint(output_dir / "last.pt", model, optimizer, scheduler, step, best_score, config)
    if cached_batches:
        log_overfit_timeline_prediction_charts(
            model=model,
            config=config,
            batches=cached_batches,
            sessions=train_sessions,
            device=device,
            wandb_run=wandb_run,
            step=step,
        )
    print_section(f"TEST END step={step:,}")
    print_section("TRAINING COMPLETE")
    print(f"*** Artifacts: {output_dir}", flush=True)
    finish_wandb_run(wandb_run)


def print_training_plan(config: ExperimentConfig, coverage: Any, planned_steps: int) -> None:
    if coverage is not None:
        max_steps = f"{planned_steps:,}" if planned_steps > 0 else f"{coverage.batches * config.train.epochs:,}"
        print(
            f"Training plan: windows={coverage.windows:,} batches_per_epoch={coverage.batches:,} "
            f"epochs={config.train.epochs} max_steps={max_steps}",
            flush=True,
        )
        return
    max_steps_text = f"{planned_steps:,}" if planned_steps > 0 else "dataset_exhaustion"
    print(
        f"Training plan: coverage_count=disabled epochs={config.train.epochs} "
        f"max_steps={max_steps_text}",
        flush=True,
    )


def print_section(title: str) -> None:
    print(LOG_RULE, flush=True)
    print(f"*** {title}", flush=True)
    print(LOG_RULE, flush=True)


def collect_overfit_batches(loader: DataLoader, target_windows: int) -> list[dict[str, Any]]:
    print_section(f"BUILDING OVERFIT CACHE target_windows={target_windows:,}")
    batches = []
    collected_windows = 0
    for batch in loader:
        remaining = target_windows - collected_windows
        if remaining <= 0:
            break
        cached = cache_batch(slice_batch(batch, remaining))
        batches.append(cached)
        collected_windows += batch_window_count(cached)
        if collected_windows >= target_windows:
            break
    if not batches:
        raise SystemExit("No overfit batches were created. Pick a session/ticker set with enough bars.")
    print_section(f"OVERFIT CACHE BUILT windows={collected_windows:,} batches={len(batches):,}")
    return batches


def slice_batch(batch: dict[str, Any], max_rows: int) -> dict[str, Any]:
    row_count = batch_window_count(batch)
    if row_count <= max_rows:
        return batch
    rows = slice(0, max_rows)
    sliced: dict[str, Any] = {}
    for key, value in batch.items():
        if torch is not None and torch.is_tensor(value):
            sliced[key] = value[rows]
        elif isinstance(value, np.ndarray):
            sliced[key] = value[rows].copy()
        elif isinstance(value, list):
            sliced[key] = list(value[:max_rows])
        else:
            sliced[key] = value
    return sliced


def batch_window_count(batch: dict[str, Any]) -> int:
    values = batch.get("values")
    if torch is not None and torch.is_tensor(values):
        return int(values.shape[0])
    if isinstance(values, np.ndarray):
        return int(values.shape[0])
    tickers = batch.get("ticker")
    if isinstance(tickers, list):
        return len(tickers)
    raise ValueError("Batch does not contain values or ticker rows, so window count cannot be determined.")


def cache_batch(batch: dict[str, Any]) -> dict[str, Any]:
    cached: dict[str, Any] = {}
    for key, value in batch.items():
        if torch is not None and torch.is_tensor(value):
            cached[key] = value.cpu()
        elif isinstance(value, np.ndarray):
            cached[key] = value.copy()
        elif isinstance(value, list):
            cached[key] = list(value)
        else:
            cached[key] = value
    return cached


def estimate_epoch(step: int, cached_batches: list[dict[str, Any]], config: TrainConfig) -> int | None:
    if cached_batches:
        return max(1, math.ceil(step / max(1, len(cached_batches))))
    if config.max_steps > 0 and config.epochs > 0:
        return min(config.epochs, max(1, math.ceil(step * config.epochs / config.max_steps)))
    return None


def log_metrics(path: Path, wandb_run: Any, row: dict[str, Any]) -> None:
    label = str(row.get("type") or "metrics")
    row = with_composite_metrics(row, label)
    append_jsonl(path, row)
    if wandb_run is None:
        return
    step = int(row.get("step") or 0)
    if label.endswith("_progress"):
        return
    payload: dict[str, float | int] = {}
    payload["train_step"] = step
    nonfinite_count = 0
    for key, value in row.items():
        if key in {"type", "time"}:
            continue
        if not should_log_wandb_scalar(label, key):
            continue
        if isinstance(value, bool):
            payload[f"{label}/{key}"] = int(value)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            payload[f"{label}/{key}"] = value
        elif isinstance(value, (int, float)):
            nonfinite_count += 1
    payload.update(wandb_metric_aliases(label, row))
    bucket_table = confidence_bucket_wandb_table(label, row)
    if bucket_table is not None:
        payload[f"{label}/confidence_calibration_buckets"] = bucket_table
    if nonfinite_count:
        payload[f"{label}/nonfinite_metric_count"] = nonfinite_count
    if payload:
        wandb_run.log(payload)


def with_composite_metrics(row: dict[str, Any], label: str) -> dict[str, Any]:
    enriched = dict(row)
    expected_mae = metric_value(enriched, label, "h1_close_expected_signed_mae_bps")
    expected_dir = metric_value(enriched, label, "h1_close_expected_dir_acc_pct")
    expected_corr = metric_value(enriched, label, "h1_close_expected_signed_corr")
    hard_mae = metric_value(enriched, label, "h1_close_hard_decoded_mae_bps")
    hard_edge_vs_last = metric_value(enriched, label, "h1_close_edge_vs_last_move_naive_bps")

    expected_edge_vs_last = math.nan
    last_move_mae = math.nan
    if is_finite_number(hard_mae) and is_finite_number(hard_edge_vs_last) and is_finite_number(expected_mae):
        last_move_mae = float(hard_mae) + float(hard_edge_vs_last)
        expected_edge_vs_last = last_move_mae - float(expected_mae)
        set_metric_value(
            enriched,
            label,
            "h1_close_expected_edge_vs_last_move_naive_bps",
            expected_edge_vs_last,
        )

    score = model_behavior_score(
        final_dir_acc_pct=expected_dir,
        final_corr=expected_corr,
        final_edge_vs_last_move_bps=expected_edge_vs_last,
        last_move_mae_bps=last_move_mae,
    )
    if is_finite_number(score):
        set_metric_value(enriched, label, "h1_close_model_behavior_score", score)
    return enriched


def metric_value(row: dict[str, Any], label: str, source: str) -> Any:
    if source in row:
        return row[source]
    prefixed = f"{label}_{source}"
    if prefixed in row:
        return row[prefixed]
    return None


def set_metric_value(row: dict[str, Any], label: str, source: str, value: float) -> None:
    prefixed = f"{label}_{source}"
    if prefixed in row or any(key.startswith(f"{label}_h1_") for key in row):
        row[prefixed] = value
    else:
        row[source] = value


def model_behavior_score(
    *,
    final_dir_acc_pct: Any,
    final_corr: Any,
    final_edge_vs_last_move_bps: Any,
    last_move_mae_bps: Any,
) -> float:
    components: list[float] = []
    if is_finite_number(final_dir_acc_pct):
        components.append(clamp(float(final_dir_acc_pct), 0.0, 100.0))
    if is_finite_number(final_corr):
        components.append(50.0 * (clamp(float(final_corr), -1.0, 1.0) + 1.0))
    if is_finite_number(final_edge_vs_last_move_bps) and is_finite_number(last_move_mae_bps):
        denominator = max(abs(float(last_move_mae_bps)), 1e-9)
        edge_ratio = float(final_edge_vs_last_move_bps) / denominator
        components.append(clamp(50.0 + 50.0 * edge_ratio, 0.0, 100.0))
    if not components:
        return math.nan
    return sum(components) / len(components)


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def should_log_wandb_scalar(label: str, key: str) -> bool:
    return key in CORE_WANDB_DIRECT_KEYS


def confidence_bucket_wandb_table(label: str, row: dict[str, Any]) -> Any | None:
    try:
        import wandb
    except ModuleNotFoundError:
        return None
    table_rows = []
    for horizon in range(1, 4):
        for low, high in CONFIDENCE_BUCKET_RANGES:
            bucket_key = f"{low:02d}_{high:02d}"
            prefix = f"{label}_h{horizon}_close_conf_bucket_{bucket_key}"
            coverage = row.get(f"{prefix}_coverage_pct")
            if not isinstance(coverage, (int, float)) or not math.isfinite(float(coverage)):
                continue
            table_rows.append(
                [
                    horizon,
                    f"{low / 100:.1f}-{high / 100:.1f}",
                    row.get(f"{prefix}_count"),
                    coverage,
                    row.get(f"{prefix}_mae_bps"),
                    row.get(f"{prefix}_dir_acc_pct"),
                    row.get(f"{prefix}_mean_abs_expected_bps"),
                    row.get(f"{prefix}_mean_abs_actual_bps"),
                    row.get(f"{prefix}_mean_magnitude_std_bps"),
                ]
            )
    if not table_rows:
        return None
    return wandb.Table(
        columns=[
            "horizon",
            "confidence_bucket",
            "count",
            "coverage_pct",
            "mae_bps",
            "dir_acc_pct",
            "mean_abs_expected_bps",
            "mean_abs_actual_bps",
            "mean_magnitude_std_bps",
        ],
        data=table_rows,
    )


def stop_training_for_nonfinite_loss(
    error: NonFiniteLossError,
    *,
    output_dir: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Any,
    step: int,
    best_score: float,
    config: ExperimentConfig,
    metrics_path: Path,
    wandb_run: Any,
) -> None:
    print_section("TRAINING STOPPED: NON-FINITE LOSS")
    print(f"*** {error}", flush=True)
    row = {
        "type": "guardrail",
        "step": step,
        "guardrail_triggered": True,
        "nonfinite_loss": True,
        "nonfinite_label": error.label,
        "nonfinite_step": error.step,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    log_metrics(metrics_path, wandb_run, row)
    write_json(
        output_dir / "nonfinite_stop.json",
        {
            **row,
            "details": error.details,
            "message": str(error),
        },
    )
    save_checkpoint(output_dir / "nonfinite.pt", model, optimizer, scheduler, step, best_score, config)
    save_checkpoint(output_dir / "last.pt", model, optimizer, scheduler, step, best_score, config)
    print(f"*** Non-finite diagnostic written to {output_dir / 'nonfinite_stop.json'}", flush=True)
    print(f"*** Non-finite checkpoint written to {output_dir / 'nonfinite.pt'}", flush=True)
    finish_wandb_run(wandb_run, exit_code=1)


def finish_wandb_run(wandb_run: Any, exit_code: int = 0) -> None:
    if wandb_run is None:
        return
    try:
        wandb_run.finish(exit_code=exit_code)
    except TypeError:
        wandb_run.finish()


def wandb_metric_aliases(label: str, row: dict[str, Any]) -> dict[str, float | int]:
    aliases: dict[str, float | int] = {}
    direct_names = {
        "loss": "loss",
        "bit_accuracy_pct": "bit_accuracy_pct",
        "lr": "lr",
        "samples_per_sec": "samples_per_sec",
        "epoch": "epoch",
        "eval_batches": "eval_batches",
    }
    prefixed_names = {
        "loss": "loss",
        "bit_accuracy_pct": "bit_accuracy_pct",
        "batches": "batches",
        "windows": "windows",
        "windows_per_sec": "windows_per_sec",
    }
    h1_names = {
        "close_hard_decoded_mae_bps": ("h1_hard_decoded_mae_bps",),
        "close_expected_signed_mae_bps": ("h1_expected_signed_mae_bps", "h1_final_mae_bps"),
        "close_expected_signed_corr": ("h1_expected_signed_corr", "h1_final_corr"),
        "close_expected_dir_acc_pct": ("h1_expected_dir_acc_pct", "h1_final_dir_acc_pct"),
        "close_expected_edge_vs_last_move_naive_bps": (
            "h1_expected_edge_vs_last_move_naive_bps",
            "h1_final_edge_vs_last_move_naive_bps",
        ),
        "close_model_behavior_score": ("h1_model_behavior_score",),
        "close_mean_confidence": ("h1_mean_confidence",),
        "close_mean_magnitude_std_bps": ("h1_mean_magnitude_std_bps",),
        "close_coverage_at_conf_0_5_pct": ("h1_coverage_at_conf_0_5_pct",),
        "close_mae_at_conf_0_5_bps": ("h1_mae_at_conf_0_5_bps",),
        "close_dir_acc_at_conf_0_5_pct": ("h1_dir_acc_at_conf_0_5_pct",),
        "close_coverage_at_conf_0_7_pct": ("h1_coverage_at_conf_0_7_pct",),
        "close_mae_at_conf_0_7_bps": ("h1_mae_at_conf_0_7_bps",),
        "close_dir_acc_at_conf_0_7_pct": ("h1_dir_acc_at_conf_0_7_pct",),
        "close_dir_acc_pct": ("h1_dir_acc_pct",),
        "close_edge_vs_last_move_naive_bps": ("h1_edge_vs_last_move_naive_bps",),
        "close_last_move_dir_acc_pct": ("h1_last_move_dir_acc_pct",),
        "close_corr": ("h1_corr",),
    }
    for source, alias in direct_names.items():
        add_wandb_alias(aliases, label, alias, row.get(source))
    for source, alias in prefixed_names.items():
        add_wandb_alias(aliases, label, alias, row.get(f"{label}_{source}"))
    for suffix, alias_names in h1_names.items():
        for alias in alias_names:
            add_wandb_alias(aliases, label, alias, row.get(f"h1_{suffix}"))
            add_wandb_alias(aliases, label, alias, row.get(f"{label}_h1_{suffix}"))
    add_priority_wandb_aliases(aliases, label, row)
    return aliases


def add_priority_wandb_aliases(aliases: dict[str, float | int], label: str, row: dict[str, Any]) -> None:
    if label.endswith("_progress"):
        return
    for alias, source in PRIORITY_WANDB_ALIASES:
        add_wandb_alias(aliases, f"priority/{label}", alias, priority_metric_value(row, label, source))


def priority_metric_value(row: dict[str, Any], label: str, source: str) -> Any:
    if source in row:
        return row[source]
    prefixed = f"{label}_{source}"
    if prefixed in row:
        return row[prefixed]
    if source.startswith("h1_"):
        compact = source.removeprefix("h1_")
        if compact in row:
            return row[compact]
        prefixed_compact = f"{label}_{compact}"
        if prefixed_compact in row:
            return row[prefixed_compact]
    return None


def add_wandb_alias(aliases: dict[str, float | int], label: str, name: str, value: Any) -> None:
    if isinstance(value, bool):
        aliases[f"{label}/{name}"] = int(value)
    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
        aliases[f"{label}/{name}"] = value


def should_log_eval_progress(batch_count: int, progress_batches: int) -> bool:
    if progress_batches <= 0:
        return False
    return batch_count == 1 or batch_count % progress_batches == 0


def build_eval_progress_metrics(
    *,
    accumulator: MetricAccumulator,
    label: str,
    loss_sum: float,
    batches: int,
    started: float,
    step: int,
    epoch: int | None,
) -> dict[str, Any]:
    progress_label = f"{label}_progress"
    elapsed = max(1e-6, time.perf_counter() - started)
    metrics = accumulator.compute(prefix=f"{progress_label}_")
    windows = int(metrics.get(f"{progress_label}_windows", 0) or 0)
    metrics.update(
        {
            "type": progress_label,
            "step": step,
            "eval_batches": batches,
            f"{progress_label}_loss": loss_sum / max(1, batches),
            f"{progress_label}_batches": batches,
            f"{progress_label}_elapsed_sec": elapsed,
            f"{progress_label}_windows_per_sec": windows / elapsed,
            **({"epoch": epoch} if epoch is not None else {}),
            "time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    return metrics


def evaluate(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    device: torch.device,
    max_windows: int,
    label: str,
    metrics_path: Path | None = None,
    wandb_run: Any = None,
    step: int = 0,
    epoch: int | None = None,
) -> dict[str, Any]:
    assert torch is not None
    model.eval()
    dataset = RollingBarWindowDataset(
        config=config.data,
        sessions=sessions,
        tickers=tickers,
        batch_size=config.train.batch_size,
        seed=config.train.seed + 100,
        mode=label,
        max_windows=max_windows,
        shuffle=False,
    )
    loader = DataLoader(dataset, batch_size=None, num_workers=0, pin_memory=False)
    accumulator = MetricAccumulator(
        horizon=config.data.horizon,
        target_columns=config.data.target_columns,
        direction_threshold_bps=config.model.direction_threshold_bps,
    )
    loss_sum = 0.0
    bit_accuracy_sum = 0.0
    batches = 0
    started = time.perf_counter()
    with torch.inference_mode():
        for batch in loader:
            batch = move_batch(batch, device)
            with torch.autocast(
                device_type=device.type,
                dtype=torch.float16,
                enabled=config.train.amp and device.type == "cuda",
            ):
                prediction = model(batch["values"], batch["time_features"])
                loss, loss_parts = forecast_loss(
                    prediction,
                    batch["targets"],
                )
            raise_on_nonfinite_loss(loss, label=label, step=step, batch=batch)
            loss_sum += float(loss.detach().cpu())
            bit_accuracy_sum += float(loss_parts.get("bit_accuracy_pct", math.nan))
            batches += 1
            prediction_bps, target_bps = prediction_and_target_bps(prediction, batch, config)
            accumulator.update(prediction_bps, target_bps, last_close_return_bps_from_batch(batch))
            update_confidence_metrics(accumulator, prediction, batch, config)
            if should_log_eval_progress(batches, config.train.eval_progress_batches):
                progress_metrics = build_eval_progress_metrics(
                    accumulator=accumulator,
                    label=label,
                    loss_sum=loss_sum,
                    batches=batches,
                    started=started,
                    step=step,
                    epoch=epoch,
                )
                if metrics_path is not None:
                    log_metrics(metrics_path, wandb_run, progress_metrics)
                print_metric_line(progress_metrics)
            del batch, prediction, loss
    if device.type == "cuda":
        torch.cuda.empty_cache()
    metrics = accumulator.compute(prefix=f"{label}_")
    metrics[f"{label}_loss"] = loss_sum / max(1, batches)
    metrics[f"{label}_bit_accuracy_pct"] = bit_accuracy_sum / max(1, batches)
    metrics[f"{label}_batches"] = batches
    metrics[f"{label}_elapsed_sec"] = time.perf_counter() - started
    return metrics


def evaluate_cached_batches(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    batches: Iterable[dict[str, Any]],
    device: torch.device,
    label: str,
) -> dict[str, Any]:
    model.eval()
    accumulator = MetricAccumulator(
        horizon=config.data.horizon,
        target_columns=config.data.target_columns,
        direction_threshold_bps=config.model.direction_threshold_bps,
    )
    loss_sum = 0.0
    bit_accuracy_sum = 0.0
    batch_count = 0
    with torch.inference_mode():
        for batch in batches:
            batch = move_batch(batch, device)
            with torch.autocast(
                device_type=device.type,
                dtype=torch.float16,
                enabled=config.train.amp and device.type == "cuda",
            ):
                prediction = model(batch["values"], batch["time_features"])
                loss, loss_parts = forecast_loss(
                    prediction,
                    batch["targets"],
                )
            raise_on_nonfinite_loss(loss, label=label, step=0, batch=batch)
            loss_sum += float(loss.detach().cpu())
            bit_accuracy_sum += float(loss_parts.get("bit_accuracy_pct", math.nan))
            batch_count += 1
            prediction_bps, target_bps = prediction_and_target_bps(prediction, batch, config)
            accumulator.update(prediction_bps, target_bps, last_close_return_bps_from_batch(batch))
            update_confidence_metrics(accumulator, prediction, batch, config)
            del batch, prediction, loss
    if device.type == "cuda":
        torch.cuda.empty_cache()
    metrics = accumulator.compute(prefix=f"{label}_")
    metrics[f"{label}_loss"] = loss_sum / max(1, batch_count)
    metrics[f"{label}_bit_accuracy_pct"] = bit_accuracy_sum / max(1, batch_count)
    metrics[f"{label}_batches"] = batch_count
    return metrics


def log_overfit_timeline_prediction_charts(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    batches: Iterable[dict[str, Any]],
    sessions: list[str],
    device: torch.device,
    wandb_run: Any,
    step: int,
    max_tickers: int = 3,
    max_points_per_ticker: int = 0,
) -> None:
    if wandb_run is None:
        return
    if "close" not in config.data.target_columns:
        print("*** Overfit timeline prediction chart skipped because close is not in target columns.", flush=True)
        return
    if not sessions:
        print("*** Overfit timeline prediction chart skipped because no training session was provided.", flush=True)
        return
    try:
        import wandb
    except ModuleNotFoundError:
        print("*** Overfit timeline prediction chart skipped because wandb is unavailable.", flush=True)
        return

    selected_tickers = top_tickers_from_cached_batches(batches, max_tickers)
    if not selected_tickers:
        print("*** Overfit timeline prediction chart skipped because cached batches had no ticker metadata.", flush=True)
        return

    session = sessions[0]
    model.eval()
    rows_by_ticker = infer_session_timeline_predictions(
        model=model,
        config=config,
        session=session,
        tickers=selected_tickers,
        device=device,
        max_points_per_ticker=max_points_per_ticker,
    )

    if not rows_by_ticker:
        print("*** Overfit timeline prediction chart skipped because the selected tickers had no valid session windows.", flush=True)
        return

    payload: dict[str, Any] = {"train_step": step}
    table_rows = []
    long_table_rows = []
    for ticker, ticker_rows in rows_by_ticker.items():
        ticker_rows.sort(key=lambda item: int(item["bar_index"]))
        safe_ticker = re.sub(r"[^A-Za-z0-9_.-]+", "_", ticker)
        x_values = [int(row["bar_index"]) for row in ticker_rows]
        target_values = [float(row["target_close"]) for row in ticker_rows]
        prediction_values = [float(row["prediction_close"]) for row in ticker_rows]
        payload[f"overfit_timeline_predictions/{safe_ticker}_h1_close_plot"] = wandb.plot.line_series(
            xs=x_values,
            ys=[target_values, prediction_values],
            keys=["target_close", "prediction_close"],
            title=f"{ticker} {session} chronological h1 close",
            xname="bar_index",
        )
        for sample_index, row in enumerate(ticker_rows):
            target_close = float(row["target_close"])
            prediction_close = float(row["prediction_close"])
            table_rows.append(
                [
                    ticker,
                    session,
                    sample_index,
                    int(row["bar_index"]),
                    str(row["target_time"]),
                    int(row["target_timestamp_ns"]),
                    float(row["current_close"]),
                    target_close,
                    prediction_close,
                    float(row["target_bps"]),
                    float(row["prediction_bps"]),
                    float(row["expected_signed_bps"]),
                    float(row["confidence"]),
                    float(row["magnitude_std_bps"]),
                    float(row["p_up"]),
                    float(row["abs_error_bps"]),
                ]
            )
            for series, close_value in (
                ("target_close", target_close),
                ("prediction_close", prediction_close),
            ):
                long_table_rows.append(
                    [
                        ticker,
                        session,
                        sample_index,
                        int(row["bar_index"]),
                        str(row["target_time"]),
                        int(row["target_timestamp_ns"]),
                        series,
                        close_value,
                    ]
                )

    payload["overfit_timeline_predictions/table"] = wandb.Table(
        columns=[
            "ticker",
            "session",
            "timeline_order",
            "bar_index",
            "target_time",
            "target_timestamp_ns",
            "current_close",
            "target_close",
            "prediction_close",
            "target_bps",
            "prediction_bps",
            "expected_signed_bps",
            "confidence",
            "magnitude_std_bps",
            "p_up",
            "abs_error_bps",
        ],
        data=table_rows,
    )
    payload["overfit_timeline_predictions/line_data"] = wandb.Table(
        columns=[
            "ticker",
            "session",
            "timeline_order",
            "bar_index",
            "target_time",
            "target_timestamp_ns",
            "series",
            "close",
        ],
        data=long_table_rows,
    )
    wandb_run.log(payload)
    print(
        "*** W&B overfit timeline prediction data and line charts logged for "
        f"{', '.join(rows_by_ticker.keys())} from session {session}",
        flush=True,
    )


def top_tickers_from_cached_batches(
    batches: Iterable[dict[str, Any]],
    max_tickers: int,
) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    for batch in batches:
        counts.update(str(ticker) for ticker in batch.get("ticker", []))
    return tuple(ticker for ticker, _ in counts.most_common(max_tickers))


def infer_session_timeline_predictions(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    session: str,
    tickers: tuple[str, ...],
    device: torch.device,
    max_points_per_ticker: int,
) -> dict[str, list[dict[str, float | int | str]]]:
    frame = load_session_frame(config.data, session, tickers)
    if frame.is_empty():
        return {}

    rows_by_ticker: dict[str, list[dict[str, float | int | str]]] = {}
    for ticker, ticker_frame in iter_ticker_frames(frame):
        if ticker not in tickers:
            continue
        rows = infer_ticker_timeline_predictions(
            model=model,
            config=config,
            ticker=ticker,
            ticker_frame=ticker_frame,
            device=device,
            max_points=max_points_per_ticker,
        )
        if rows:
            rows_by_ticker[ticker] = rows
    return rows_by_ticker


def infer_ticker_timeline_predictions(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    ticker: str,
    ticker_frame: Any,
    device: torch.device,
    max_points: int,
) -> list[dict[str, float | int | str]]:
    arrays = ticker_arrays(ticker_frame, config.data)
    current_session = str(ticker_frame["session_date"][0])
    origins = valid_origins(arrays, current_session, config.data)
    if max_points > 0:
        origins = origins[:max_points]
    if origins.size == 0:
        return []

    rows: list[dict[str, float | int | str]] = []
    batch_builder = BatchBuilder(
        batch_size=config.train.batch_size,
        context_length=config.data.context_length,
        feature_count=len(config.data.input_feature_columns),
        time_feature_count=len(config.data.time_feature_columns),
        horizon=config.data.horizon,
        target_count=len(config.data.target_columns),
        target_bit_count=target_bit_count(config.data),
    )
    pending_origins: list[int] = []
    with torch.inference_mode():
        for origin in origins:
            batch_builder.add(arrays, int(origin), config.data, ticker=ticker)
            pending_origins.append(int(origin))
            if batch_builder.full:
                rows.extend(
                    infer_timeline_batch_rows(
                        model=model,
                        config=config,
                        batch=batch_builder.as_torch(),
                        origins=pending_origins,
                        device=device,
                    )
                )
                batch_builder = batch_builder.empty_like()
                pending_origins = []
        if len(batch_builder) > 0:
            rows.extend(
                infer_timeline_batch_rows(
                    model=model,
                    config=config,
                    batch=batch_builder.as_torch(),
                    origins=pending_origins,
                    device=device,
                )
            )
    return rows


def infer_timeline_batch_rows(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    batch: dict[str, Any],
    origins: list[int],
    device: torch.device,
) -> list[dict[str, float | int | str]]:
    close_index = config.data.target_columns.index("close")
    device_batch = move_batch(batch, device)
    with torch.autocast(
        device_type=device.type,
        dtype=torch.float16,
        enabled=config.train.amp and device.type == "cuda",
    ):
        prediction = model(device_batch["values"], device_batch["time_features"])
    prediction_prices, target_prices = prediction_and_target_prices(prediction, device_batch, config)
    prediction_bps, target_bps = prediction_and_target_bps(prediction, device_batch, config)
    confidence_stats = prediction_confidence_stats(prediction) if config.data.target_mode == "binary_magnitude_bps" else {}
    timestamps_ns = batch["target_timestamp_ns"].detach().cpu().numpy()
    current_close = batch["current_close"].detach().cpu().numpy()
    tickers = batch.get("ticker", [""] * len(origins))
    rows = []
    for row_index, origin in enumerate(origins):
        target_bps_value = float(target_bps[row_index, 0, close_index])
        prediction_bps_value = float(prediction_bps[row_index, 0, close_index])
        expected_bps_value = _stats_value(confidence_stats, "expected_signed_bps", row_index, close_index)
        confidence_value = _stats_value(confidence_stats, "confidence", row_index, close_index)
        magnitude_std_value = _stats_value(confidence_stats, "magnitude_std_bps", row_index, close_index)
        p_up_value = _stats_value(confidence_stats, "p_up", row_index, close_index)
        timestamp_ns = int(timestamps_ns[row_index])
        rows.append(
            {
                "ticker": str(tickers[row_index]),
                "bar_index": int(origin + 1),
                "target_time": datetime.fromtimestamp(timestamp_ns / 1_000_000_000).isoformat(timespec="seconds"),
                "target_timestamp_ns": timestamp_ns,
                "current_close": float(current_close[row_index]),
                "target_close": float(target_prices[row_index, 0, close_index]),
                "prediction_close": float(prediction_prices[row_index, 0, close_index]),
                "target_bps": target_bps_value,
                "prediction_bps": prediction_bps_value,
                "expected_signed_bps": expected_bps_value,
                "confidence": confidence_value,
                "magnitude_std_bps": magnitude_std_value,
                "p_up": p_up_value,
                "abs_error_bps": abs(prediction_bps_value - target_bps_value),
            }
        )
    return rows


def _stats_value(stats: dict[str, np.ndarray], key: str, row_index: int, close_index: int) -> float:
    value = stats.get(key)
    if value is None:
        return math.nan
    return float(value[row_index, 0, close_index])


def batch_metrics_from_prediction(
    prediction: torch.Tensor,
    batch: dict[str, Any],
    config: ExperimentConfig,
) -> dict[str, float]:
    prediction_bps, target_bps = prediction_and_target_bps(prediction, batch, config)
    accumulator = MetricAccumulator(
        horizon=config.data.horizon,
        target_columns=config.data.target_columns,
        direction_threshold_bps=config.model.direction_threshold_bps,
    )
    accumulator.update(prediction_bps, target_bps, last_close_return_bps_from_batch(batch))
    update_confidence_metrics(accumulator, prediction, batch, config)
    computed = accumulator.compute()
    return {
        "h1_close_mae_bps": float(computed.get("h1_close_mae_bps", math.nan)),
        "h1_close_expected_signed_mae_bps": float(
            computed.get("h1_close_expected_signed_mae_bps", math.nan)
        ),
        "h1_close_dir_acc_pct": float(computed.get("h1_close_dir_acc_pct", math.nan)),
        "h1_close_expected_dir_acc_pct": float(computed.get("h1_close_expected_dir_acc_pct", math.nan)),
        "h1_close_mean_confidence": float(computed.get("h1_close_mean_confidence", math.nan)),
        "h1_close_mean_magnitude_std_bps": float(computed.get("h1_close_mean_magnitude_std_bps", math.nan)),
        "h1_close_edge_vs_naive_bps": float(computed.get("h1_close_edge_vs_naive_bps", math.nan)),
        "h1_close_last_move_naive_mae_bps": float(computed.get("h1_close_last_move_naive_mae_bps", math.nan)),
        "h1_close_edge_vs_last_move_naive_bps": float(
            computed.get("h1_close_edge_vs_last_move_naive_bps", math.nan)
        ),
        "h1_close_last_move_dir_acc_pct": float(computed.get("h1_close_last_move_dir_acc_pct", math.nan)),
        "h1_close_mean_reversion_naive_mae_bps": float(
            computed.get("h1_close_mean_reversion_naive_mae_bps", math.nan)
        ),
        "h1_close_edge_vs_mean_reversion_naive_bps": float(
            computed.get("h1_close_edge_vs_mean_reversion_naive_bps", math.nan)
        ),
        "h1_close_mean_reversion_dir_acc_pct": float(
            computed.get("h1_close_mean_reversion_dir_acc_pct", math.nan)
        ),
    }


def update_confidence_metrics(
    accumulator: MetricAccumulator,
    prediction: torch.Tensor,
    batch: dict[str, Any],
    config: ExperimentConfig,
) -> None:
    if config.data.target_mode != "binary_magnitude_bps":
        return
    stats = prediction_confidence_stats(prediction)
    accumulator.update_confidence(
        expected_signed_bps=stats["expected_signed_bps"],
        target=batch["target_bps"].detach().cpu().numpy(),
        confidence=stats["confidence"],
        magnitude_std_bps=stats["magnitude_std_bps"],
        p_up=stats["p_up"],
        sign_confidence=stats["sign_confidence"],
    )


def prediction_confidence_stats(prediction: torch.Tensor) -> dict[str, np.ndarray]:
    return binary_magnitude_logits_to_distribution_stats(prediction.detach().cpu().numpy())


def prediction_and_target_bps(
    prediction: torch.Tensor,
    batch: dict[str, Any],
    config: ExperimentConfig,
) -> tuple[np.ndarray, np.ndarray]:
    prediction_np = prediction.detach().cpu().numpy()
    target_np = batch["targets"].detach().cpu().numpy()
    if config.data.target_mode == "return_bps":
        return prediction_np, target_np
    if config.data.target_mode == "binary_magnitude_bps":
        current_close = batch["current_close"].detach().cpu().numpy()
        target_center = batch["target_center"].detach().cpu().numpy()
        target_scale = batch["target_scale"].detach().cpu().numpy()
        prediction_bps = target_values_to_bps(
            prediction_np,
            current_close,
            target_center,
            target_scale,
            config.data.target_mode,
        )
        target_bps = batch["target_bps"].detach().cpu().numpy()
        return prediction_bps, target_bps
    current_close = batch["current_close"].detach().cpu().numpy()
    target_center = batch["target_center"].detach().cpu().numpy()
    target_scale = batch["target_scale"].detach().cpu().numpy()
    prediction_bps = target_values_to_bps(
        prediction_np,
        current_close,
        target_center,
        target_scale,
        config.data.target_mode,
    )
    target_bps = target_values_to_bps(
        target_np,
        current_close,
        target_center,
        target_scale,
        config.data.target_mode,
    )
    return prediction_bps, target_bps


def last_close_return_bps_from_batch(batch: dict[str, Any]) -> np.ndarray | None:
    value = batch.get("last_close_return_bps")
    if value is None:
        return None
    if torch.is_tensor(value):
        return value.detach().cpu().numpy()
    return np.asarray(value, dtype=np.float64)


def prediction_and_target_prices(
    prediction: torch.Tensor,
    batch: dict[str, Any],
    config: ExperimentConfig,
) -> tuple[np.ndarray, np.ndarray]:
    prediction_np = prediction.detach().cpu().numpy()
    target_np = batch["targets"].detach().cpu().numpy()
    current_close = batch["current_close"].detach().cpu().numpy()
    if config.data.target_mode == "actual_price_zscore":
        target_center = batch["target_center"].detach().cpu().numpy().reshape(-1, 1, 1)
        target_scale = batch["target_scale"].detach().cpu().numpy().reshape(-1, 1, 1)
        return prediction_np * target_scale + target_center, target_np * target_scale + target_center
    if config.data.target_mode == "return_bps":
        current = np.maximum(current_close.reshape(-1, 1, 1), 1e-6)
        return current * np.exp(prediction_np / 10000.0), current * np.exp(target_np / 10000.0)
    if config.data.target_mode == "binary_magnitude_bps":
        prediction_bps, target_bps = prediction_and_target_bps(prediction, batch, config)
        current = np.maximum(current_close.reshape(-1, 1, 1), 1e-6)
        return current * np.exp(prediction_bps / 10000.0), current * np.exp(target_bps / 10000.0)
    raise ValueError(f"Unsupported target_mode: {config.data.target_mode}")


def print_metric_line(metrics: dict[str, Any]) -> None:
    label = str(metrics["type"])
    step = metrics.get("step", 0)
    parts = [
        f"{label} step={step:,}",
        f"loss={metrics.get(f'{label}_loss', 0.0):.6f}",
        f"windows={metrics.get(f'{label}_windows', 0):,}",
    ]
    if "lr" in metrics:
        parts.append(f"lr={float(metrics['lr']):.3e}")
    for horizon in range(1, 4):
        mae_key = f"{label}_h{horizon}_close_mae_bps"
        dir_key = f"{label}_h{horizon}_close_dir_acc_pct"
        expected_mae_key = f"{label}_h{horizon}_close_expected_signed_mae_bps"
        expected_dir_key = f"{label}_h{horizon}_close_expected_dir_acc_pct"
        confidence_key = f"{label}_h{horizon}_close_mean_confidence"
        edge_key = f"{label}_h{horizon}_close_edge_vs_naive_bps"
        last_move_edge_key = f"{label}_h{horizon}_close_edge_vs_last_move_naive_bps"
        last_move_dir_key = f"{label}_h{horizon}_close_last_move_dir_acc_pct"
        if mae_key in metrics:
            parts.append(
                f"h{horizon}_mae={metrics[mae_key]:.3f}bps "
                f"h{horizon}_dir={metrics[dir_key]:.2f}% "
                f"h{horizon}_edge={metrics[edge_key]:.3f}bps"
            )
            if expected_mae_key in metrics:
                parts.append(
                    f"h{horizon}_exp_mae={metrics[expected_mae_key]:.3f}bps "
                    f"h{horizon}_exp_dir={metrics[expected_dir_key]:.2f}% "
                    f"h{horizon}_conf={metrics[confidence_key]:.3f}"
                )
            if last_move_edge_key in metrics:
                parts.append(
                    f"h{horizon}_edge_vs_last={metrics[last_move_edge_key]:.3f}bps "
                    f"h{horizon}_last_dir={metrics[last_move_dir_key]:.2f}%"
                )
    print(" | ".join(parts), flush=True)


def step_text(step: int, planned_steps: int) -> str:
    return f"{step:,}/{planned_steps:,}" if planned_steps > 0 else f"{step:,}"


def resolve_device(requested: str) -> torch.device:
    assert torch is not None
    if requested == "cuda" and not torch.cuda.is_available():
        print("CUDA requested but unavailable; using CPU.", flush=True)
        return torch.device("cpu")
    return torch.device(requested)


def move_batch(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    moved: dict[str, Any] = {}
    for key, value in batch.items():
        if torch.is_tensor(value):
            moved[key] = value.to(device, non_blocking=True)
        else:
            moved[key] = value
    return moved


def raise_on_nonfinite_loss(loss: torch.Tensor, *, label: str, step: int, batch: dict[str, torch.Tensor]) -> None:
    if bool(torch.isfinite(loss).detach().cpu()):
        return
    details = []
    for key in ("values", "time_features", "targets"):
        tensor = batch.get(key)
        if tensor is None:
            continue
        finite_mask = torch.isfinite(tensor)
        finite_count = int(finite_mask.sum().detach().cpu())
        total_count = tensor.numel()
        if finite_count:
            finite_values = tensor[finite_mask]
            min_value = float(finite_values.min().detach().cpu())
            max_value = float(finite_values.max().detach().cpu())
        else:
            min_value = math.nan
            max_value = math.nan
        details.append(
            f"{key}: finite={finite_count}/{total_count} min={min_value:.6g} max={max_value:.6g}"
        )
    joined = "; ".join(details)
    raise NonFiniteLossError(label=label, step=step, details=joined)


def lr_multiplier(step: int, warmup_steps: int, total_steps: int) -> float:
    if warmup_steps > 0 and step < warmup_steps:
        return max(1e-4, float(step + 1) / float(warmup_steps))
    if total_steps <= 0:
        return 1.0
    if total_steps <= warmup_steps:
        return 1.0
    progress = min(1.0, (step - warmup_steps) / float(total_steps - warmup_steps))
    return 0.1 + 0.9 * 0.5 * (1.0 + math.cos(math.pi * progress))


def set_optimizer_base_lrs(optimizer: torch.optim.Optimizer) -> None:
    for group in optimizer.param_groups:
        group["base_lr"] = float(group["lr"])


def make_scheduler(
    optimizer: torch.optim.Optimizer,
    config: TrainConfig,
    planned_steps: int,
) -> Any:
    if config.lr_scheduler == "plateau":
        return torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode="min",
            factor=config.lr_plateau_factor,
            patience=config.lr_plateau_patience,
            threshold=config.lr_plateau_threshold,
            threshold_mode="rel",
            min_lr=config.min_learning_rate,
        )
    if config.lr_scheduler == "cosine":
        return torch.optim.lr_scheduler.LambdaLR(
            optimizer,
            lr_lambda=lambda step: lr_multiplier(step, config.warmup_steps, planned_steps),
        )
    if config.lr_scheduler == "cosine_warm_restarts":
        t0_steps = cosine_restart_t0_steps(config)
        return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer,
            T_0=t0_steps,
            T_mult=max(1, int(config.cosine_restart_t_mult)),
            eta_min=config.min_learning_rate,
        )
    return None


def apply_pre_step_lr(optimizer: torch.optim.Optimizer, config: TrainConfig, step: int) -> None:
    if config.lr_scheduler in {"cosine", "cosine_warm_restarts"} or config.warmup_steps <= 0 or step > config.warmup_steps:
        return
    multiplier = max(1e-4, float(step) / float(config.warmup_steps))
    for group in optimizer.param_groups:
        group["lr"] = max(config.min_learning_rate, float(group["base_lr"]) * multiplier)


def step_batch_scheduler(scheduler: Any, config: TrainConfig, step: int) -> None:
    if config.lr_scheduler in {"cosine", "cosine_warm_restarts"} and scheduler is not None:
        scheduler.step()


def cosine_restart_t0_steps(config: TrainConfig) -> int:
    if config.cosine_restart_t0_steps > 0:
        return config.cosine_restart_t0_steps
    if config.eval_steps > 0:
        return config.eval_steps
    return 25


def apply_validation_scheduler(
    scheduler: Any,
    optimizer: torch.optim.Optimizer,
    config: TrainConfig,
    validation_metrics: dict[str, Any],
    step: int,
) -> None:
    validation_loss = validation_metrics.get("validation_loss")
    old_lr = float(optimizer.param_groups[0]["lr"])
    if config.lr_scheduler == "plateau" and scheduler is not None and validation_loss is not None:
        if step > config.warmup_steps:
            scheduler.step(float(validation_loss))
    new_lr = float(optimizer.param_groups[0]["lr"])
    validation_metrics["lr"] = new_lr
    if new_lr < old_lr:
        validation_metrics["lr_reduced"] = True
        validation_metrics["lr_before"] = old_lr
        print(f"*** LR REDUCED ON PLATEAU | step={step:,} | {old_lr:.3e} -> {new_lr:.3e}", flush=True)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)


def make_output_dir(config: ExperimentConfig) -> Path:
    root = config.data.processed_root / "models" / "inhouse_transformer" / EXPERIMENT_VERSION
    if config.train.output_name:
        return root / config.train.output_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = (
        f"feature_temporal_{EXPERIMENT_VERSION}_{config.data.target_mode}_ctx{config.data.context_length}_h{config.data.horizon}_"
        f"{config.data.train_start_date}_{config.data.test_end_date}_{timestamp}"
    )
    return root / name


def save_checkpoint(
    path: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Any,
    step: int,
    best_score: float,
    config: ExperimentConfig,
) -> None:
    assert torch is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict() if scheduler is not None else None,
            "step": step,
            "best_score": best_score,
            "config": config_to_dict(config),
        },
        path,
    )


def maybe_resume(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Any,
    output_dir: Path,
    resume_latest: bool,
    device: torch.device,
) -> tuple[int, float]:
    assert torch is not None
    if not resume_latest:
        return 0, math.inf
    checkpoint_path = output_dir / "last.pt"
    if not checkpoint_path.exists():
        print(f"*** RESUME REQUESTED but no checkpoint found at {checkpoint_path}; starting fresh.", flush=True)
        return 0, math.inf
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    set_optimizer_base_lrs(optimizer)
    if scheduler is not None and checkpoint.get("scheduler") is not None:
        try:
            scheduler.load_state_dict(checkpoint["scheduler"])
        except Exception as exc:
            print(f"Checkpoint scheduler state was not loaded because it is incompatible: {exc}", flush=True)
    step = int(checkpoint.get("step") or 0)
    best_score = float(checkpoint.get("best_score") or math.inf)
    print(f"*** RESUMED CHECKPOINT | path={checkpoint_path} | step={step:,} | best_score={best_score:.4f}", flush=True)
    return step, best_score


def metadata_payload(
    config: ExperimentConfig,
    train_sessions: list[str],
    validation_sessions: list[str],
    test_sessions: list[str],
    tickers: tuple[str, ...],
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "experiment_version": EXPERIMENT_VERSION,
        "output_dir": str(output_dir),
        "data": {
            **config_to_dict(config)["data"],
            "train_sessions": len(train_sessions),
            "train_actual_start": train_sessions[0],
            "train_actual_end": train_sessions[-1],
            "validation_sessions": len(validation_sessions),
            "validation_actual_start": validation_sessions[0],
            "validation_actual_end": validation_sessions[-1],
            "test_sessions": len(test_sessions),
            "test_actual_start": test_sessions[0],
            "test_actual_end": test_sessions[-1],
            "selected_tickers": len(tickers) if tickers else "all",
        },
        "model": config_to_dict(config)["model"],
        "train": config_to_dict(config)["train"],
    }


def print_split_summary(metadata: dict[str, Any]) -> None:
    data = metadata["data"]
    print("Dataset split:", flush=True)
    print(
        f"  train: {data['train_actual_start']} -> {data['train_actual_end']} "
        f"({data['train_sessions']} sessions)",
        flush=True,
    )
    print(
        f"  validation: {data['validation_actual_start']} -> {data['validation_actual_end']} "
        f"({data['validation_sessions']} sessions)",
        flush=True,
    )
    print(
        f"  test: {data['test_actual_start']} -> {data['test_actual_end']} "
        f"({data['test_sessions']} sessions)",
        flush=True,
    )
    print(
        f"  selected_tickers: {data['selected_tickers']} "
        f"carry_context_across_session={data['carry_context_across_session']} "
        f"allow_target_across_session={data['allow_target_across_session']}",
        flush=True,
    )


def config_to_dict(config: ExperimentConfig) -> dict[str, Any]:
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "data": {
            "processed_root": str(config.data.processed_root),
            "train_start_date": config.data.train_start_date,
            "train_end_date": config.data.train_end_date,
            "validation_start_date": config.data.validation_start_date,
            "validation_end_date": config.data.validation_end_date,
            "test_start_date": config.data.test_start_date,
            "test_end_date": config.data.test_end_date,
            "timeframe": config.data.timeframe,
            "session_scope": config.data.session_scope,
            "context_length": config.data.context_length,
            "horizon": config.data.horizon,
            "target_mode": config.data.target_mode,
            "binary_magnitude_bits": config.data.binary_magnitude_bits,
            "target_columns": list(config.data.target_columns),
            "input_normalization": config.data.input_normalization,
            "input_feature_columns": list(config.data.input_feature_columns),
            "time_feature_columns": list(config.data.time_feature_columns),
            "tickers": list(config.data.tickers),
            "max_tickers": config.data.max_tickers,
            "allow_target_across_session": config.data.allow_target_across_session,
            "carry_context_across_session": config.data.carry_context_across_session,
        },
        "model": {
            "architecture_name": MODEL_ARCHITECTURE_NAME,
            "d_model": config.model.d_model,
            "feature_attention_layers": config.model.feature_attention_layers,
            "feature_attention_chunk_size": config.model.feature_attention_chunk_size,
            "temporal_layers": config.model.temporal_layers,
            "num_heads": config.model.num_heads,
            "ff_dim": config.model.ff_dim,
            "dropout": config.model.dropout,
            "target_bit_count": config.model.target_bit_count,
            "direction_threshold_bps": config.model.direction_threshold_bps,
        },
        "train": {
            "batch_size": config.train.batch_size,
            "epochs": config.train.epochs,
            "max_steps": config.train.max_steps,
            "learning_rate": config.train.learning_rate,
            "weight_decay": config.train.weight_decay,
            "warmup_steps": config.train.warmup_steps,
            "lr_scheduler": config.train.lr_scheduler,
            "lr_plateau_factor": config.train.lr_plateau_factor,
            "lr_plateau_patience": config.train.lr_plateau_patience,
            "lr_plateau_threshold": config.train.lr_plateau_threshold,
            "cosine_restart_t0_steps": config.train.cosine_restart_t0_steps,
            "cosine_restart_effective_t0_steps": (
                cosine_restart_t0_steps(config.train)
                if config.train.lr_scheduler == "cosine_warm_restarts"
                else 0
            ),
            "cosine_restart_t_mult": config.train.cosine_restart_t_mult,
            "min_learning_rate": config.train.min_learning_rate,
            "grad_clip_norm": config.train.grad_clip_norm,
            "logging_steps": config.train.logging_steps,
            "eval_steps": config.train.eval_steps,
            "eval_progress_batches": config.train.eval_progress_batches,
            "validation_window_count": config.train.validation_window_count,
            "test_window_count": config.train.test_window_count,
            "max_batches_per_session": config.train.max_batches_per_session,
            "count_coverage": config.train.count_coverage,
            "num_workers": config.train.num_workers,
            "seed": config.train.seed,
            "amp": config.train.amp,
            "compile_model": config.train.compile_model,
            "output_name": config.train.output_name,
            "resume_latest": config.train.resume_latest,
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_torch_stack() -> None:
    global DataLoader, FeatureTemporalTransformer, forecast_loss, torch
    if torch is not None:
        return
    try:
        import torch as torch_module
        from torch.utils.data import DataLoader as data_loader_class

        from research.inhouse_transformer.v16.model import (
            FeatureTemporalTransformer as transformer_class,
            forecast_loss as loss_function,
        )
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PyTorch is required for training. Activate the training environment first, "
            "for example your ml4t environment, then rerun this script."
        ) from exc
    torch = torch_module
    DataLoader = data_loader_class
    FeatureTemporalTransformer = transformer_class
    forecast_loss = loss_function


if __name__ == "__main__":
    main()

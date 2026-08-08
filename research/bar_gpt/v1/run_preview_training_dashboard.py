from __future__ import annotations

import argparse
import time

from rich.console import Console

from research.bar_gpt.v1.progress import TrainingProgressState, TrainingReporter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the BarGPT training dashboard with synthetic data only."
    )
    parser.add_argument("--width", type=int, default=160, help="preview width in terminal columns")
    parser.add_argument("--height", type=int, default=42, help="preview height in terminal rows")
    parser.add_argument(
        "--empty",
        action="store_true",
        help="show the startup schema with unavailable values instead of representative values",
    )
    return parser


def _state() -> TrainingProgressState:
    return TrainingProgressState(
        run_name="medium-16x2-epoch1-preview",
        device="cuda:0 NVIDIA RTX workstation",
        precision="bfloat16",
        output_dir=r"D:\TradingML\runtimes\bar_gpt\v1\train\preview",
        model_parameters=87_432_704,
        max_samples=402_653_184,
        epochs_total=1,
        epoch_index=1,
        epoch_origin_budget=402_653_184,
        planned_units=2_352,
        planned_blocks=98_304,
        gradient_accumulation_steps=2,
        cuda_prefetch=True,
        origin_bars=4_096,
        warmup_samples=12_079_596,
        schedule_samples=402_653_184,
        unit_plans={"MSFT:2020-07": (44, 180_224)},
        validation_runs_total=100,
    )


def main() -> None:
    args = _parser().parse_args()
    if args.width < 120 or args.height < 36:
        raise SystemExit("preview requires at least --width 120 and --height 36")
    state = _state()
    reporter = TrainingReporter(state, layout="none")
    if not args.empty:
        state.state = "running"
        reporter.started = time.perf_counter() - 3_782.0
        reporter.update(
            {
                "train/samples_seen": 117_440_512,
                "train/batches_seen": 896,
                "train/optimizer_steps": 448,
                "train/blocks_seen": 28_672,
                "train/units_seen": 694,
                "train/condition_blocks_seen": 12_441,
                "train/loss": 0.184271,
                "train/loss_autoregressive": 0.112420,
                "train/loss_horizon": 0.066118,
                "train/loss_ar_continuous": 0.091351,
                "train/loss_ar_availability": 0.021069,
                "train/loss_horizon_quantile": 0.052944,
                "train/loss_horizon_availability": 0.013174,
                "train/loss_latent_prediction": 0.005733,
                "train/gradient_norm": 0.8421,
                "train/condition_positive_rate": 0.0047,
                "train/learning_rate": 2.74e-4,
                "train/amp_scale": 1.0,
                "train/origins_per_second": 65_114.0,
                "train/loader_wait_seconds": 0.018,
                "train/gpu_seconds": 1.917,
                "train/gpu_duty_cycle": 0.972,
                "train/host_cache_batches": 11,
                "train/host_cache_capacity": 16,
            },
            tickers=("AAPL", "MSFT"),
            dates=("2020-07-14", "2020-07-15"),
            unit_indices=(693,),
            block_offsets=(27,),
        )
        state.epoch_origins_seen = state.samples_seen
        state.validation_runs_completed = 28
        reporter.validation(
            {
                "validation_loss/total": 0.201844,
                "validation_loss/autoregressive": 0.121992,
                "validation_loss/horizon": 0.073891,
                "validation_return/mae_bps_macro": 7.438,
                "validation_direction/balanced_accuracy_macro": 0.5632,
                "validation_direction/mcc_macro": 0.1267,
                "validation_calibration/error_macro": 0.0281,
                "validation_availability/brier_macro": 0.0874,
                "validation_ranking/spearman_macro": 0.0942,
                "validation_confidence/top_10pct_accuracy_macro": 0.6194,
                "validation_confidence/top_20pct_accuracy_macro": 0.6011,
                "validation_data/origins": 802_816,
                "validation_data/batches": 196,
                "validation_coverage_q10/macro": 0.1092,
                "validation_coverage_q50/macro": 0.5071,
                "validation_coverage_q90/macro": 0.8879,
            }
        )
        reporter.schedule_validation(120_795_955)
        state.last_checkpoint = r"D:\TradingML\runtimes\bar_gpt\v1\train\preview\checkpoint_latest.pt"
        reporter.messages.clear()
        reporter.messages.extend(
            (
                "14:01:08 Certified shard plan loaded",
                "14:01:11 Epoch 1/1 started",
                "15:03:52 Validation completed: loss=0.201844",
                "15:04:10 Training prefetch resumed from durable cursors",
            )
        )
    console = Console(width=args.width, height=args.height, force_terminal=True)
    reporter._console = console
    console.print(reporter.render())


if __name__ == "__main__":
    main()

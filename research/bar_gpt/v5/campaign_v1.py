"""Full-epoch MAE orchestration; model mathematics remain in encoder_v1/trainer_v1.

Inputs are the sealed split manifest and training-only ActivityStats produced by
data_v1. Loaders deliver PreparedBatch tensors, with eight/nine visible market
columns over 64 bars. This module owns scheduling, validation and durable state.
"""

from dataclasses import asdict, replace
import math
import shutil

import torch

from .data_v1 import close_loader
from .encoder_v1 import MaskedAutoencoderV1
from .engine_v1 import run_steps
from .shards.contract import write_json


class EpochCosine:
    """Linear update warmup, then epoch-wise cosine from peak to final-epoch minimum.

    Epoch zero uses the peak after warmup. Epoch N-1 uses the minimum. Epoch-wise
    decay avoids guessing eligible batch counts from the raw-candidate manifest.
    The state is ordinary serializable scalars; optimizer state is saved separately.
    """

    def __init__(self, peak, minimum, epochs, warmup):
        self.peak, self.minimum = peak, minimum
        self.epochs, self.warmup = epochs, warmup
        self.epoch = self.updates = 0

    def value(self):
        if self.updates < self.warmup:
            fraction = (self.updates + 1) / self.warmup
            return self.minimum + (self.peak - self.minimum) * fraction
        fraction = self.epoch / max(1, self.epochs - 1)
        return self.minimum + (self.peak - self.minimum) * (1 + math.cos(math.pi * fraction)) / 2

    def state_dict(self):
        return dict(vars(self))

    def load_state_dict(self, state):
        self.__dict__.update(state)


def train_campaign(args, manifest, stats, reporter):
    """Train/validate complete epochs and atomically publish resumable last/best states.

    Every epoch file contains FP32 model/AdamW state, scheduler position, RNGs,
    immutable data contract, validation score, and metric history. Validation
    uses epoch zero's fixed masks and never changes the optimizer or scheduler.
    """
    from .run_v1 import CONFIGURATIONS, loader_for

    torch.manual_seed(args.seed)
    model = MaskedAutoencoderV1(replace(
        CONFIGURATIONS[args.model], attention=args.attention,
        attention_dropout=args.attention_dropout,
    )).cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01, fused=True)
    scheduler = EpochCosine(args.learning_rate, args.min_learning_rate, args.epochs, args.warmup_updates)
    contract = dict(
        split_hash=manifest["manifest_hash"], model=asdict(model.config), batch=args.batch_size,
        workers=args.workers, precision=args.precision, seed=args.seed,
        median=stats.median.tolist(), scale=stats.scale.tolist(),
        train_steps=args.train_steps, validation_steps=args.validation_steps,
        epochs=args.epochs, peak=args.learning_rate, minimum=args.min_learning_rate,
        warmup=args.warmup_updates, data_order="eligible-stream-v1",
    )
    epoch = step = 0
    phase, history, best, training_metrics = "train", [], None, None
    if args.resume:
        saved = torch.load(args.resume, map_location="cpu", weights_only=False)
        if saved["contract"] != contract:
            raise ValueError("Resume requires identical data, model, optimizer and schedule contracts.")
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        scheduler.load_state_dict(saved["scheduler"])
        epoch, step, phase = saved["epoch"], saved["step"], saved["phase"]
        history, best, training_metrics = saved["history"], saved["best_validation"], saved["training_metrics"]
        torch.set_rng_state(saved["rng"])
        torch.cuda.set_rng_state_all(saved["cuda_rng"])

    def save(name):
        """Replace only a fully written checkpoint; failed writes preserve the previous file."""
        if not torch.stack([torch.isfinite(p).all() for p in model.parameters()]).all():
            raise FloatingPointError("Refusing to publish nonfinite model weights.")
        state = dict(
            contract=contract, model=model.state_dict(), optimizer=optimizer.state_dict(),
            scheduler=scheduler.state_dict(), epoch=epoch, step=step, phase=phase,
            history=history, best_validation=best, training_metrics=training_metrics,
            rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all(),
        )
        temporary = args.output / (name + ".partial")
        torch.save(state, temporary)
        temporary.replace(args.output / name)

    def evaluate_or_train(split):
        """One iterator per full split; worker prefetch persists throughout that pass."""
        training = split == "train"
        loader = loader_for(args, manifest, stats, split, args.batch_size, args.workers, epoch if training else 0)
        limit = (args.train_steps if training else args.validation_steps) or len(loader)
        reporter.update(
            stage="starting " + split + " loader", epoch=epoch + 1, epochs=args.epochs,
            best_validation=best, total_is_upper_bound=True,
            config=dict(name=args.model, batch_size=args.batch_size, workers=args.workers,
                        precision=args.precision, prefetch=args.prefetch),
        )

        def set_rate(_):
            optimizer.param_groups[0]["lr"] = scheduler.value()

        def advance_schedule(_):
            scheduler.updates += 1  # Exactly one increment after each successful optimizer update.

        try:
            result = run_steps(
                model, optimizer, loader, steps=limit, precision=args.precision,
                training=training, reporter=reporter, log_base=step, seconds_limit=float("inf"),
                stop_path=args.output / "STOP", metrics_every=args.metrics_every, loss_every=args.loss_every,
                before_step=set_rate if training else None, on_step=advance_schedule if training else None,
            )
            result["complete_pass"] = result["exhausted"] or result["consumed_batches"] >= limit
            return result
        finally:
            close_loader(loader)
            if "result" in locals():
                scanned, excluded, prepared = loader.dataset.counters.sum(0).tolist()
                result.update(scanned_candidates=scanned, excluded_no_price=excluded,
                              prepared_batches=prepared)

    # Initial state allows a failed/interrupted first epoch to restart without losing initialization.
    save("checkpoint_last.pt")
    while epoch < args.epochs and not (args.output / "STOP").exists():
        scheduler.epoch = epoch
        if phase == "train":
            training_metrics = evaluate_or_train("train")
            if not training_metrics["complete_pass"]:
                break  # Last checkpoint resumes this epoch from its start; partial epochs are not promoted.
            step += training_metrics["consumed_batches"]
            phase = "validation"
            save("checkpoint_last.pt")  # A validation interruption does not require retraining the epoch.
        validation = evaluate_or_train("validation")
        if not validation["complete_pass"]:
            break
        score = validation["loss"]
        improved = best is None or score < best
        best = score if improved else best
        epoch += 1
        phase = "train"
        history.append(dict(epoch=epoch, training=training_metrics, validation=validation,
                            learning_rate=optimizer.param_groups[0]["lr"], best_validation=best))
        reporter.log({"epoch": epoch, "epoch/train_loss": training_metrics["loss"],
                      "epoch/validation_loss": score, "epoch/best_validation_loss": best}, step)
        reporter.update(epoch=epoch, best_validation=best, validation_loss=score, stage="saving epoch checkpoint")
        save(f"checkpoint_epoch_{epoch:03d}.pt")
        # Copy to a temporary path before replacing the operational resume/best aliases.
        for alias in ["checkpoint_last.pt"] + (["checkpoint_best.pt"] if improved else []):
            temporary = args.output / (alias + ".partial")
            shutil.copyfile(args.output / f"checkpoint_epoch_{epoch:03d}.pt", temporary)
            temporary.replace(args.output / alias)
        write_json(args.output / "results.json", dict(history=history, best_validation=best))
    return dict(history=history, best_validation=best, checkpoint=str(args.output / "checkpoint_last.pt"))

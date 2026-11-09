"""Full-epoch MAE orchestration; model mathematics remain in encoder_v1/trainer_v1.

Inputs are the sealed split manifest and training-only ActivityStats produced by
data_v1. Loaders deliver PreparedBatch tensors, with seven/eight visible market
columns over 64 bars. This module owns scheduling, validation and durable state.
"""

from dataclasses import asdict, replace
from copy import deepcopy
import json
import math
import shutil

import torch

from .data_v1 import close_loader
from .encoder_v1 import MaskedAutoencoderV1
from .engine_v1 import run_steps
from .shards.contract import write_json
from .shards.phase1_contract import VERSION


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
        return (
            self.minimum
            + (self.peak - self.minimum) * (1 + math.cos(math.pi * fraction)) / 2
        )

    def state_dict(self):
        return dict(vars(self))

    def load_state_dict(self, state):
        self.__dict__.update(state)


def compatible_resume_contract(saved, current):
    """Allow only the explicit overfit stop-policy change; keep training state exact.

    Inputs are checkpoint contract dictionaries, without tensors. Legacy guards
    used ``patience`` for both checks. The no-learning patience, probe population,
    data/model/optimization contract and schedule must still match exactly.
    """
    left, right = deepcopy(saved), deepcopy(current)
    for contract in (left, right):
        if "learning_guard" in contract:
            contract["learning_guard"].pop("overfit_patience", None)
    return left == right


def preserve_prior_best(checkpoint, output, best, contract, best_epoch):
    """Copy the unchanged best checkpoint from the resume ancestry, never overwrite it.

    An attempt may have no improvement, so its best file can live in an earlier
    attempt. Check its scalar score and full contract on CPU before publication.
    Parent manifests supply the explicit resume chain; cycles fail closed.
    """
    seen = set()
    while checkpoint:
        root = checkpoint.parent
        if root in seen:
            raise ValueError("Cyclic best-checkpoint ancestry")
        seen.add(root)
        for candidate in (
            root / f"checkpoint_epoch_{best_epoch:03d}.pt",
            root / "checkpoint_best.pt",
        ):
            if not candidate.is_file():
                continue
            state = torch.load(candidate, map_location="cpu", weights_only=False)
            if state["best_validation"] == best and compatible_resume_contract(
                state["contract"], contract
            ):
                temporary = output / "checkpoint_best.pt.partial"
                shutil.copyfile(candidate, temporary)
                temporary.replace(output / "checkpoint_best.pt")
                write_json(
                    output / "best-checkpoint-origin.json",
                    dict(source=str(candidate), best_validation=best),
                )
                return
        manifest = root / "manifest.json"
        previous = (
            json.loads(manifest.read_text()).get("resume")
            if manifest.exists()
            else None
        )
        checkpoint = type(checkpoint)(previous) if previous else None
    raise ValueError("Cannot locate the saved best checkpoint in the resume ancestry")


def train_campaign(args, manifest, stats, reporter):
    """Train/validate complete epochs and atomically publish resumable last/best states.

    Every epoch file contains FP32 model/AdamW state, scheduler position, RNGs,
    immutable data contract, validation score, and metric history. Validation
    uses epoch zero's fixed masks and never changes the optimizer or scheduler.
    """
    from .run_v1 import CONFIGURATIONS, loader_for

    torch.manual_seed(args.seed)
    model = MaskedAutoencoderV1(
        replace(
            CONFIGURATIONS[args.model],
            attention=args.attention,
            attention_dropout=args.attention_dropout,
        )
    ).cuda()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=0.01, fused=True
    )
    scheduler = EpochCosine(
        args.learning_rate, args.min_learning_rate, args.epochs, args.warmup_updates
    )
    contract = dict(
        split_hash=manifest["manifest_hash"],
        model=asdict(model.config),
        batch=args.batch_size,
        workers=args.workers,
        precision=args.precision,
        seed=args.seed,
        median=stats.median.tolist(),
        scale=stats.scale.tolist(),
        train_steps=args.train_steps,
        validation_steps=args.validation_steps,
        epochs=args.epochs,
        peak=args.learning_rate,
        minimum=args.min_learning_rate,
        warmup=args.warmup_updates,
        data_order="eligible-stream-v1",
        input_schema=VERSION,
        market_channels=18,
        time_channels=9,
        mask_rate=0.6,
    )
    epoch = step = 0
    guarded = getattr(args, "learning_guard", False)
    requested_patience = getattr(args, "overfit_patience", None)
    overfit_patience = requested_patience or 3
    probe_baseline = None
    if guarded:
        contract["learning_guard"] = dict(
            version=1,
            probe_steps=args.guard_probe_steps,
            probe_workers=min(4, args.workers),
            patience=3,
        )
        if overfit_patience != 3:
            contract["learning_guard"]["overfit_patience"] = overfit_patience
    phase, history, best, training_metrics = "train", [], None, None
    transition, best_epoch = None, None
    if args.resume:
        saved = torch.load(args.resume, map_location="cpu", weights_only=False)
        old_guard = saved["contract"].get("learning_guard", {})
        old_patience = old_guard.get("overfit_patience", old_guard.get("patience", 3))
        overfit_patience = (
            requested_patience if requested_patience is not None else old_patience
        )
        if guarded and overfit_patience != 3:
            contract["learning_guard"]["overfit_patience"] = overfit_patience
        if saved.get("data_transition"):
            from .data_transition_v1 import validate_contract

            calibration = json.loads((args.output / "calibration.json").read_text())
            contract = validate_contract(saved, contract, manifest, calibration)
            transition = deepcopy(saved["data_transition"])
        if not compatible_resume_contract(saved["contract"], contract):
            raise ValueError(
                "Resume requires identical data, model, optimizer and schedule contracts."
            )
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        scheduler.load_state_dict(saved["scheduler"])
        epoch, step, phase = saved["epoch"], saved["step"], saved["phase"]
        history, best, training_metrics = (
            saved["history"],
            saved["best_validation"],
            saved["training_metrics"],
        )
        probe_baseline = saved.get("probe_baseline")
        best_epoch = saved.get("best_epoch")
        if old_patience != overfit_patience:
            write_json(
                args.output / "guard-policy-change.json",
                dict(
                    checkpoint=str(args.resume),
                    old_overfit_patience=old_patience,
                    overfit_patience=overfit_patience,
                    no_learning_patience=3,
                ),
            )
        if best is not None:
            if best_epoch is None:
                best_epoch = min(history, key=lambda row: row["validation"]["loss"])[
                    "epoch"
                ]
            preserve_prior_best(args.resume, args.output, best, contract, best_epoch)
        torch.set_rng_state(saved["rng"])
        torch.cuda.set_rng_state_all(saved["cuda_rng"])

    def save(name):
        """Replace only a fully written checkpoint; failed writes preserve the previous file."""
        if not torch.stack([torch.isfinite(p).all() for p in model.parameters()]).all():
            raise FloatingPointError("Refusing to publish nonfinite model weights.")
        state = dict(
            contract=contract,
            model=model.state_dict(),
            optimizer=optimizer.state_dict(),
            scheduler=scheduler.state_dict(),
            epoch=epoch,
            step=step,
            phase=phase,
            history=history,
            best_validation=best,
            best_epoch=best_epoch,
            training_metrics=training_metrics,
            probe_baseline=probe_baseline,
            rng=torch.get_rng_state(),
            cuda_rng=torch.cuda.get_rng_state_all(),
        )
        if transition is not None:
            state["data_transition"] = deepcopy(transition)
        temporary = args.output / (name + ".partial")
        torch.save(state, temporary)
        temporary.replace(args.output / name)

    def evaluate_or_train(
        split, probe=False, *, evaluated_model=None, metric_role=None
    ):
        """One iterator per full split; worker prefetch persists throughout that pass."""
        training = split == "train"
        if training and evaluated_model is not None:
            raise ValueError("Alternate models are evaluation-only")
        loader = loader_for(
            args,
            manifest,
            stats,
            split,
            args.batch_size,
            min(4, args.workers) if probe else args.workers,
            epoch if training else 0,
            # Complete validation is an order-independent reduction. Let ready
            # batches bypass a slow worker, retaining every worker's original
            # batches and mask seeds. Bounded learning probes MUST stay ordered
            # so their sample subset remains identical to its frozen baseline.
            ordered=training or probe or args.validation_steps != 0,
        )
        limit = (args.train_steps if training else args.validation_steps) or len(loader)
        if probe:
            limit = min(len(loader), args.guard_probe_steps)
        reporter.update(
            stage="starting " + split + " loader",
            epoch=epoch + 1,
            epochs=args.epochs,
            done=0,
            total=limit,  # Replace the prior pass's counters before workers start or prefetch.
            best_validation=best,
            total_is_upper_bound=True,
            config=dict(
                name=args.model,
                batch_size=args.batch_size,
                workers=args.workers,
                precision=args.precision,
                prefetch=args.prefetch,
            ),
        )

        def set_rate(_):
            optimizer.param_groups[0]["lr"] = scheduler.value()

        def advance_schedule(_):
            scheduler.updates += (
                1  # Exactly one increment after each successful optimizer update.
            )

        try:
            result = run_steps(
                model if evaluated_model is None else evaluated_model,
                optimizer,
                loader,
                steps=limit,
                precision=args.precision,
                training=training,
                reporter=reporter,
                log_base=step,
                seconds_limit=float("inf"),
                stop_path=args.output / "STOP",
                metrics_every=args.metrics_every,
                loss_every=args.loss_every,
                before_step=set_rate if training else None,
                on_step=advance_schedule if training else None,
                metric_prefix=metric_role or ("probe" if probe else None),
                status_stage=(
                    metric_role.replace("_", " ")
                    if metric_role
                    else ("learning probe" if probe else None)
                ),
            )
            result["complete_pass"] = (
                result["exhausted"] or result["consumed_batches"] >= limit
            )
            reporter.update(
                done=result["consumed_batches"], total=limit, metrics=result
            )
            # Flush final progress even when a pass ends between the ten-step status updates.
            return result
        finally:
            close_loader(loader)
            if "result" in locals():
                scanned, excluded, prepared = loader.dataset.counters.sum(0).tolist()
                result.update(
                    scanned_candidates=scanned,
                    excluded_no_price=excluded,
                    prepared_batches=prepared,
                )

    if (
        transition is not None
        and transition["state"] == "requires-corrected-data-baselines"
    ):
        from .data_transition_v1 import collect_baselines
        from .shards.contract import digest

        # Baselines include a full validation pass. Preserve the pending state
        # first so interruption retries evaluation without replaying an epoch.
        save("checkpoint_last.pt")

        def baseline_evaluate(selected, probe):
            role = (
                "untrained_probe"
                if selected is not model
                else ("trained_probe" if probe else "trained_validation")
            )
            return evaluate_or_train(
                "validation",
                probe=probe,
                evaluated_model=selected,
                metric_role="data_revision_" + role,
            )

        baselines = collect_baselines(
            model,
            lambda: MaskedAutoencoderV1(model.config).cuda(),
            baseline_evaluate,
            args.seed,
        )
        evidence = dict(
            epoch=epoch,
            step=step,
            split_hash=manifest["manifest_hash"],
            certificate=transition["certificate"]["hash"],
            baselines=baselines,
        )
        evidence["hash"] = digest(evidence)
        transition.update(state="baselines-complete", baseline_evidence=evidence)
        probe_baseline = baselines["untrained_probe"]["loss"]
        best, best_epoch = baselines["trained_validation"]["loss"], epoch
        write_json(args.output / "data-transition-baseline.json", evidence)
        reporter.log(
            {
                "data_revision/untrained_probe_loss": probe_baseline,
                "data_revision/trained_probe_loss": baselines["trained_probe"]["loss"],
                "data_revision/baseline_validation_loss": best,
                "data_revision/certificate": transition["certificate"]["hash"],
            },
            step,
        )
        save("checkpoint_best.pt")

    # Initial state allows a failed/interrupted first epoch to restart without losing initialization.
    if guarded and probe_baseline is None:
        probe_baseline = evaluate_or_train("validation", probe=True)["loss"]
        write_json(
            args.output / "learning-baseline.json",
            dict(
                loss=probe_baseline,
                probe_steps=args.guard_probe_steps,
                masks_epoch=0,
                split_hash=manifest["manifest_hash"],
            ),
        )
    save("checkpoint_last.pt")
    while epoch < args.epochs and not (args.output / "STOP").exists():
        scheduler.epoch = epoch
        if phase == "train":
            training_metrics = evaluate_or_train("train")
            if not training_metrics["complete_pass"]:
                break  # Last checkpoint resumes this epoch from its start; partial epochs are not promoted.
            step += training_metrics["consumed_batches"]
            phase = "validation"
            save(
                "checkpoint_last.pt"
            )  # A validation interruption does not require retraining the epoch.
        validation = evaluate_or_train("validation")
        if not validation["complete_pass"]:
            break
        score = validation["loss"]
        improved = best is None or score < best
        if improved:
            best_epoch = epoch + 1
        best = score if improved else best
        epoch += 1
        phase = "train"
        history.append(
            dict(
                epoch=epoch,
                training=training_metrics,
                validation=validation,
                learning_rate=optimizer.param_groups[0]["lr"],
                best_validation=best,
            )
        )
        if guarded:
            from .learning_guard_v1 import assess

            history[-1]["probe"] = evaluate_or_train("validation", probe=True)
            decision = assess(
                history, probe_baseline, overfit_patience=overfit_patience
            )
            history[-1]["learning_guard"] = decision
            write_json(
                args.output / "learning-review.json",
                dict(epoch=epoch, baseline=probe_baseline, **decision),
            )
        reporter.log(
            {
                "epoch": epoch,
                "epoch/train_loss": training_metrics["loss"],
                "epoch/validation_loss": score,
                "epoch/best_validation_loss": best,
                "epoch/full_validation_samples_per_second": validation[
                    "samples_per_second"
                ],
                **(
                    {
                        "epoch/probe_samples_per_second": history[-1]["probe"][
                            "samples_per_second"
                        ]
                    }
                    if guarded
                    else {}
                ),
            },
            step,
        )
        reporter.update(
            epoch=epoch,
            best_validation=best,
            validation_loss=score,
            stage="saving epoch checkpoint",
        )
        save(f"checkpoint_epoch_{epoch:03d}.pt")
        # Copy to a temporary path before replacing the operational resume/best aliases.
        for alias in ["checkpoint_last.pt"] + (
            ["checkpoint_best.pt"] if improved else []
        ):
            temporary = args.output / (alias + ".partial")
            shutil.copyfile(args.output / f"checkpoint_epoch_{epoch:03d}.pt", temporary)
            temporary.replace(args.output / alias)
        write_json(
            args.output / "results.json", dict(history=history, best_validation=best)
        )
        if guarded and decision["action"] == "stop":
            # Preserve this epoch and the earlier best before requesting a stop.
            # The controller may investigate/restart in a separate experiment.
            (args.output / "STOP").write_text(
                "Learning guard: " + decision["reason"], encoding="utf-8"
            )
            break
    return dict(
        history=history,
        best_validation=best,
        checkpoint=str(args.output / "checkpoint_last.pt"),
    )

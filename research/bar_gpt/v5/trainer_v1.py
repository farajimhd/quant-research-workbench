"""Minimal MAE trainer, separate from architecture and dataset/job engineering.

The caller supplies re-iterable CPU batches and fitted training-only statistics.
No shard discovery, workstation launch, progress server, or automatic long run.
"""

from collections.abc import Iterable
from dataclasses import asdict, dataclass

import torch
from torch import Tensor
from torch.nn import functional as F

from research.bar_gpt.v5.encoder_v1 import MaskedAutoencoderV1
from research.bar_gpt.v5.pretraining_v1 import ActivityStats, PreparedBatch, prepare_batch


@dataclass(frozen=True)
class RawBatch:
    """CPU shard tensors: values/valid [B,64,18], ends [B,64], duration scalar/[B]."""

    values: Tensor
    valid: Tensor
    end_us: Tensor
    timeframe_us: Tensor | int


def reconstruction_loss(predictions: Tensor, batch: PreparedBatch) -> tuple[Tensor, Tensor, Tensor]:
    """Masked-only, equal-feature loss; return scalar plus sums/counts [18].

    Predictions/targets are [B,M,64]; no visible values participate in this loss.
    All 18 scaled market channels use SmoothL1(beta=1). Average valid observations per feature, then equally average
    features present in the batch. Invalid source evidence never becomes a target.
    """
    if predictions.shape != batch.targets.shape or batch.targets.numel() == 0:
        raise ValueError("Reconstruction requires matching predictions and observed hidden targets.")
    errors = F.smooth_l1_loss(predictions, batch.targets, reduction="none")
    errors = torch.where(batch.target_valid, errors, 0)  # [B,M,64], remove unavailable evidence.
    sums = torch.zeros(18, device=predictions.device).scatter_add(
        0, batch.hidden_ids.flatten(), errors.sum(-1).flatten(),
    )  # Reduce observations into semantic column IDs, independent of random ordering.
    counts = torch.zeros(18, dtype=torch.int64, device=predictions.device).scatter_add(
        0, batch.hidden_ids.flatten(), batch.target_valid.sum(-1).flatten(),
    )
    present = counts > 0
    # Fixed-size arithmetic avoids a CUDA synchronization for boolean indexing.
    loss = ((sums / counts.clamp_min(1)) * present).sum() / present.sum().clamp_min(1)
    return loss, sums.detach(), counts.detach()


class MAETrainerV1:
    """Train the column MAE with CPU mask preparation and compact device transfers.

    Construct with a model and already-fitted ActivityStats. train_epoch accepts
    Iterable[RawBatch] (a loader may prefetch CPU batches); evaluate uses an isolated
    fixed masking seed and never changes training RNG or fits scaling statistics.
    This readable baseline uses FP32 and AdamW; compilation, AMP, and job orchestration
    are deliberately left to later measured engineering work.

    Example (the caller supplies calibration tensors and a re-iterable CPU loader)::

        stats = fit_activity_stats(calibration_values, calibration_valid)
        trainer = MAETrainerV1(MaskedAutoencoderV1(), stats, device="cuda")
        for epoch in range(epochs):
            metrics = trainer.train_epoch(training_loader)
            validation = trainer.evaluate(validation_loader)

    Import fit_activity_stats from pretraining_v1. Each loader yields RawBatch;
    training/validation membership and checkpoint paths remain explicit caller choices.
    """

    def __init__(self, model: MaskedAutoencoderV1, stats: ActivityStats, *, device: str = "cpu",
                 learning_rate: float = 1e-4, weight_decay: float = 0.01, seed: int = 0):
        if learning_rate <= 0 or weight_decay < 0:
            raise ValueError("Learning rate must be positive and weight decay nonnegative.")
        self.device = torch.device(device)
        self.model = model.to(self.device)
        self.stats = ActivityStats(stats.median.detach().clone(), stats.scale.detach().clone())
        self.optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
        self.generator = torch.Generator(device="cpu").manual_seed(seed)  # Independent mask stream.
        self.steps = 0

    def _epoch(self, batches: Iterable[RawBatch], *, training: bool, generator: torch.Generator) -> dict:
        """Shared bounded-iterator loop; aggregate exact per-feature evidence counts."""
        previous_mode = self.model.training
        self.model.train(training)
        sums = torch.zeros(18, dtype=torch.float64, device=self.device)  # Stable accumulation over long epochs.
        counts = torch.zeros(18, dtype=torch.int64, device=self.device)  # Exact counts beyond FP32's integer range.
        samples = masked_columns = batches_seen = 0
        try:
            for raw in batches:
                packed = prepare_batch(raw.values, raw.valid, raw.end_us, raw.timeframe_us, self.stats, generator)
                batch = packed.to(self.device, pin_memory=self.device.type == "cuda")
                with torch.set_grad_enabled(training):
                    _, predictions = self.model(
                        batch.visible, batch.valid, batch.feature_ids, batch.hidden_ids, batch.time, batch.time_valid,
                    )
                    loss, batch_sums, batch_counts = reconstruction_loss(predictions, batch)
                    if not torch.isfinite(loss):
                        raise FloatingPointError("Nonfinite reconstruction loss; optimizer was not advanced.")
                    if training:
                        self.optimizer.zero_grad(set_to_none=True)
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0, error_if_nonfinite=True)
                        self.optimizer.step()
                        self.steps += 1
                sums += batch_sums
                counts += batch_counts
                samples += len(raw.values)
                masked_columns += batch.hidden_ids.numel()
                batches_seen += 1
        finally:
            self.model.train(previous_mode)  # Evaluation must not leave the caller in a different mode.
        if not batches_seen:
            raise ValueError("No batches supplied; an epoch cannot silently succeed empty.")
        present = counts > 0
        per_feature = sums / counts.clamp_min(1)
        return {
            "loss": float(per_feature[present].mean()),
            "feature_loss": [float(value) if observed else None for value, observed in zip(per_feature.cpu(), present.cpu())],
            "target_counts": counts.cpu().tolist(), "samples": samples, "batches": batches_seen,
            "mask_fraction": masked_columns / (samples * 18), "optimizer_steps": self.steps,
        }

    def train_epoch(self, batches: Iterable[RawBatch]) -> dict:
        """Train once over caller-provided CPU batches; return loss/count/masking metrics."""
        return self._epoch(batches, training=True, generator=self.generator)

    def evaluate(self, batches: Iterable[RawBatch], *, seed: int = 12345) -> dict:
        """Evaluate fixed masks for a fixed batch order; no gradient updates or scaler fitting."""
        return self._epoch(batches, training=False, generator=torch.Generator().manual_seed(seed))

    def state_dict(self) -> dict:
        """Return resumable state for caller-owned checkpoint storage under the runtime root.

        Call torch.save immediately if persisting: model/optimizer tensors are live
        references. Dataset position and loader RNG are caller-owned and must also
        be recorded for exact data-order replay. Architecture is checked on restore.
        """
        return {
            "version": "column-mae-v1", "config": asdict(self.model.config),
            "model": self.model.state_dict(), "optimizer": self.optimizer.state_dict(),
            "median": self.stats.median, "scale": self.stats.scale, "steps": self.steps,
            "mask_rng": self.generator.get_state(), "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if self.device.type == "cuda" else None,
        }

    def load_state_dict(self, state: dict) -> None:
        """Restore trusted checkpoint state; caller supplies matching data position separately."""
        if state["version"] != "column-mae-v1" or state["config"] != asdict(self.model.config):
            raise ValueError("Checkpoint architecture/version does not match this model.")
        self.model.load_state_dict(state["model"])
        self.optimizer.load_state_dict(state["optimizer"])
        self.stats = ActivityStats(state["median"].cpu().clone(), state["scale"].cpu().clone())
        self.steps = state["steps"]
        self.generator.set_state(state["mask_rng"].cpu())
        torch.set_rng_state(state["torch_rng"].cpu())
        if state["cuda_rng"] is not None and self.device.type == "cuda":
            torch.cuda.set_rng_state_all([item.cpu() for item in state["cuda_rng"]])

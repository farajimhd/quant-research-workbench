"""Conservative epoch decisions for unattended MAE training; no model mutation.

The fixed probe uses the same validation batches and masks as its untrained
baseline. Full validation determines best checkpoints and overfit evidence.
Training and validation losses have different dropout/masking populations, so
their absolute gap alone never triggers a stop.
"""

import math


def assess(history, baseline, patience=3, *, overfit_patience=None):
    """Return an auditable decision from completed epoch metrics, never partial passes.

    history items contain training.loss, validation.loss and probe.loss scalars.
    Stop for nonfinite evidence, three epochs without 0.5% probe improvement,
    or ``overfit_patience`` consecutive validations >2% above an earlier best while training
    loss improves >1%. A first-epoch probe regression >50% is gross divergence.
    """
    overfit_patience = patience if overfit_patience is None else overfit_patience
    if overfit_patience < 1:
        raise ValueError("Overfit patience must be positive")
    if not history:
        return dict(action="continue", reason="awaiting first completed epoch")
    scores = [float(row["validation"]["loss"]) for row in history]
    probes = [float(row["probe"]["loss"]) for row in history]
    if not all(math.isfinite(value) for value in [baseline, *scores, *probes]):
        return dict(action="stop", reason="nonfinite learning evidence")
    improvement = (baseline - min(probes)) / max(abs(baseline), 1e-12)
    if probes[-1] > max(1e-12, baseline) * 1.5:
        return dict(
            action="stop", reason="gross divergence", probe_improvement=improvement
        )
    if len(history) >= patience and improvement < 0.005:
        return dict(
            action="stop",
            reason="no measurable learning",
            probe_improvement=improvement,
        )
    best_index = min(range(len(scores)), key=scores.__getitem__)
    if len(scores) - best_index - 1 >= overfit_patience:
        deteriorating = all(
            value > scores[best_index] * 1.02 for value in scores[-overfit_patience:]
        )
        train_improved = (
            history[-1]["training"]["loss"]
            < history[best_index]["training"]["loss"] * 0.99
        )
        if deteriorating and train_improved:
            return dict(
                action="stop",
                reason="persistent overfit",
                best_epoch=history[best_index].get("epoch", best_index + 1),
                probe_improvement=improvement,
            )
    return dict(
        action="continue",
        reason="learning or insufficient persistent evidence",
        probe_improvement=improvement,
    )

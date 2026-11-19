"""Window-weighted mean of the actual training objective over evaluation batches."""
import math
import torch
from .model import objective


class ObjectiveAccumulator:
    def __init__(self, visible=16):
        self.visible = visible
        self.sums = None
        self.windows = 0
        self.keys = None

    @torch.no_grad()
    def add(self, model, out, target, valid, hidden):
        # Evaluation scores all 64 targets in all-visible mode, so its scoring
        # mask is all True even though the encoder saw the entire window.
        positions = torch.arange(64,device=target.device).expand(len(target),-1) if self.visible==64 else (~hidden).nonzero(as_tuple=True)[1].reshape(len(target),self.visible)
        take = lambda value: value.gather(1, positions[..., None].expand(-1, -1, value.shape[-1]))
        loss, parts = objective(model, out, target, valid, hidden, take(target), take(valid))
        self.keys = ['loss'] + ['objective_' + key for key in parts]
        values = torch.stack([loss, *parts.values()]).double() * len(target)
        self.sums = values if self.sums is None else self.sums + values
        self.windows += len(target)

    def finish(self):
        if not self.windows:
            raise ValueError('Empty validation objective')
        values = (self.sums / self.windows).cpu().tolist()
        if not all(math.isfinite(value) for value in values):
            raise FloatingPointError('Nonfinite validation objective')
        return dict(zip(self.keys, values))

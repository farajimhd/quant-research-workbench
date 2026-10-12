"""Closed-loop intent feedback; never an execution or label-state authority.

Each sequence must be prepared at contiguous, authenticated one-second clocks.
Eight unsupervised warm-up decisions precede the supervised target. Feedback is
detached (one-step truncated backpropagation); the representation and heads at
the target still receive the ordinary supervised losses. Missing decisions and
session boundaries reset memory rather than substituting a sampled prior row.
"""
import torch
from torch import nn

VERSION = 'v6-cross-decision-intent-feedback-v1'
WARMUP = 8
CHANNELS = ('ENTRY', 'WAIT', 'HOLD', 'EXIT', 'ENTRY_quality',
            'EXIT_quality', 'allocation_ratio', 'age_seconds', 'available')


class IntentFeedback(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.fuse = nn.Sequential(nn.Linear(width + 9, width), nn.GELU(),
                                  nn.LayerNorm(width))

    def forward(self, base, clocks_us, available, reset, held, heads, size):
        """Return chronological representations and predictions without targets.

        `held` is observed/reference position state, independently prepared.
        Predicted ENTRY intent never creates a position or a broker fill.
        Base features must already obey source_close < their decision clock.
        """
        n, steps, _ = base.shape
        shape = (n, steps)
        if steps != WARMUP + 1 or clocks_us.shape != shape:
            raise ValueError('Expected eight warm-up clocks and one target')
        if clocks_us.dtype != torch.int64:
            raise ValueError('Decision clocks must be integer microseconds')
        if any(x.shape != shape or x.dtype != torch.bool for x in (available, reset, held)):
            raise ValueError('Availability, reset and held must be boolean grids')
        if not torch.all(clocks_us[:, 1:] - clocks_us[:, :-1] == 1_000_000):
            raise ValueError('Feedback requires contiguous actual 1s clocks')
        if not available[:, -1].all():
            raise ValueError('The supervised target must be available')
        memory = base.new_zeros(n, 7)
        prior_valid = torch.zeros(n, device=base.device, dtype=torch.bool)
        contexts, predictions = [], []
        for step in range(steps):
            valid = available[:, step]
            use = prior_valid & valid & ~reset[:, step]
            feedback = torch.cat((memory * use[:, None],
                                  use[:, None].to(base.dtype),
                                  use[:, None].to(base.dtype)), -1)
            context = self.fuse(torch.cat((base[:, step], feedback), -1))
            output = heads(context, held[:, step])
            prediction = torch.cat((output.logits.softmax(-1), output.quality,
                                    size(context).sigmoid().reshape(n, 1)), -1)
            memory = prediction.detach() * valid[:, None]
            prior_valid = valid
            contexts.append(context)
            predictions.append(prediction)
        return torch.stack(contexts, 1), torch.stack(predictions, 1)

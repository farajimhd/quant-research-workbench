"""Enriched teacher plus bounded closed-loop prior-model intent.

Frozen phases cache only base representations, not feedback-conditioned ones:
changing heads changes the previous predictions and therefore current inputs.
"""
import torch

from research.rl_trading.v6.enriched_teacher import EnrichedTeacher
from research.rl_trading.v6.causal_feedback import IntentFeedback, WARMUP
from research.rl_trading.v6.transformer_search import TransformerSpec


class FeedbackTeacher(EnrichedTeacher):
    def __init__(self, spec, base, extra, normalization):
        super().__init__(spec, base, extra, normalization)
        self.intent_feedback = IntentFeedback(spec.width)

    def base_sequence(self, sequence, pacer=None):
        n, steps = sequence['clocks_us'].shape
        if steps != WARMUP + 1:
            raise ValueError('Bounded contiguous prefix required')
        # Eight-example maximum per encoder call, including warm-up decisions.
        flat = {name: value.reshape(n * steps, *value.shape[2:]) for name, value in
                sequence.items() if name in ('raw', 'present', 'held', 'multi',
                                             'multi_present', 'liquidity', 'market')}
        values = []
        for begin in range(0, n * steps, 8):
            if pacer is not None: pacer.begin_activity()
            batch = {name: value[begin:begin + 8] for name, value in flat.items()}
            values.append(super().representation(batch.pop('raw'), batch.pop('present'),
                                                  batch.pop('held'), **batch))
            if pacer is not None: pacer()
        return torch.cat(values).reshape(n, steps, -1)

    def feedback_context(self, base, sequence):
        contexts, predictions = self.intent_feedback(base, sequence['clocks_us'],
            sequence['available'], sequence['reset'], sequence['held'][..., 0] > 0,
            self.heads, self.size)
        return contexts[:, -1], predictions

    def head_parameters(self):
        super().head_parameters()
        for parameter in self.intent_feedback.parameters():
            parameter.requires_grad_(True)
        return [p for p in self.parameters() if p.requires_grad]


def build(row, base, extra, normalization):
    return FeedbackTeacher(TransformerSpec(**row['transformer']), base, extra, normalization)

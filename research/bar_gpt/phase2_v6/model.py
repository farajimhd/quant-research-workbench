"""Learn temporal summaries over ordered windows of frozen physical bar embeddings."""
import torch
from torch import nn
from ..phase2_v5.model import Forecast, Residual, SLOTS
from ..phase2_v5.run_train import ForecastEvaluation, ForecastGradients


class Head(nn.Module):
    def __init__(self, parent):
        super().__init__()
        self.context, self.heads = parent.context, parent.heads

    def encode(self, summaries):
        return self.context(summaries.flatten(1))

    def forward(self, summaries):
        return self.heads(self.encode(summaries)).view(-1, SLOTS, 32)


class BarForecast(nn.Module):
    def __init__(self, parent: Forecast):
        super().__init__()
        residual = Residual(64)
        nn.init.zeros_(residual.branch[-1].weight)
        nn.init.zeros_(residual.branch[-1].bias)
        self.temporal = nn.Sequential(parent.compress, nn.GELU(), residual)
        self.head = Head(parent)
        self.register_buffer("offsets", torch.arange(-63, 1), persistent=False)

    def summaries(self, inputs):
        bank, ends = inputs
        # Gradients from repeated endpoints accumulate on the current update.
        # Reusing these summaries after AdamW steps would give stale activations.
        unique, reverse = torch.unique(ends.flatten(), return_inverse=True)
        windows = bank[unique[:, None] + self.offsets]
        return self.temporal(windows.flatten(1))[reverse].view(*ends.shape, 64)

    def encode(self, inputs):
        return self.head.encode(self.summaries(inputs))

    def forward(self, inputs):
        return self.head(self.summaries(inputs))


class CachedGradients(ForecastGradients):
    """Reuse the fixed-shape objective graph while differentiating ragged temporal inputs."""
    def __init__(self, model, teacher, args):
        super().__init__(model.head, teacher, args)
        self.forecaster = model
        self.graph_input_grads = {}

    def window_buffer(self, n):
        return None

    def backward(self, inputs, logs, valid, *anchors):
        leaf = inputs.detach().requires_grad_()
        loss = super().backward(leaf, logs, valid, *anchors)
        self.input_grad = leaf.grad
        return loss

    def __call__(self, inputs, logs, valid, *anchors):
        summaries = self.forecaster.summaries(inputs)
        n = len(summaries)
        bucket = self.bucket(n)
        loss = super().__call__(summaries.detach(), logs, valid, *anchors)
        if bucket in self.graphs:
            # During capture, autograd fixes the leaf gradient's storage. Replay
            # rewrites it for the current batch, including after switching buckets.
            grad = self.graph_input_grads.setdefault(bucket, self.input_grad)[:n]
        else:
            grad = self.input_grad
        summaries.backward(grad)
        return loss


class CachedEvaluation(ForecastEvaluation):
    def __init__(self, model, teacher, args):
        super().__init__(model.head, teacher, args)
        self.forecaster = model

    def window_buffer(self, n):
        return None

    def __call__(self, inputs, logs, valid, evidence, anchors):
        return super().__call__(self.forecaster.summaries(inputs), logs, valid, evidence, anchors)

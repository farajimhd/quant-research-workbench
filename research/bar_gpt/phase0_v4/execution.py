"""Graph replay shares the exact objective with eager training; weights stay FP32."""

import torch
from .data import forward_batch
from .objectives import objective


def backward(model, batch):
    """The same model, objective and gradient clipping as the actual trainer."""
    out, values, valid = forward_batch(model, batch)
    loss, parts = objective(model, out, values, valid)
    loss.backward()
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=False)
    return (
        loss.detach(),
        norm.detach(),
        out["logs"].detach(),
        {k: v.detach() for k, v in parts.items()},
    )


class Replay:
    """Capture forward/backward only; optimizer updates remain separately visible."""

    def __init__(self, model, batch):
        self.batch = batch.map(lambda value: value.clone())
        batch = self.batch
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                model.zero_grad(set_to_none=True)
                backward(model, batch)
        torch.cuda.current_stream().wait_stream(stream)
        model.zero_grad(set_to_none=True)
        self.graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph, stream=stream):
            self.output = backward(model, batch)
        self.parameters = list(model.parameters())
        self.gradients = [p.grad for p in self.parameters]

    def __call__(self, batch=None):
        if batch is not None:
            for name in ("grid", "slots", "valid", "frames"):
                getattr(self.batch, name).copy_(getattr(batch, name))
        # Other evaluations can replace p.grad; graph buffers themselves persist.
        for parameter, gradient in zip(self.parameters, self.gradients):
            parameter.grad = gradient
        self.graph.replay()
        return self.output

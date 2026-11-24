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
        rng = torch.cuda.get_rng_state()
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
        self.parameters = [p for p in model.parameters() if p.requires_grad]
        self.gradients = [p.grad for p in self.parameters]
        # Warmup/capture is engineering work, not sampled training. Restoring the
        # default generator keeps mask traversal identical after a resume.
        torch.cuda.set_rng_state(rng)

    def __call__(self, batch=None):
        if batch is not None:
            for name in ("grid", "slots", "valid", "frames"):
                getattr(self.batch, name).copy_(getattr(batch, name))
        # Other evaluations can replace p.grad; graph buffers themselves persist.
        for parameter, gradient in zip(self.parameters, self.gradients):
            parameter.grad = gradient
        self.graph.replay()
        return self.output


class EvaluationReplay:
    """Replay inference and all existing metric reductions, not a sampled subset.

    Warmup allocates diagnostic tables. Reset only accumulators afterward;
    constants and their addresses must remain fixed for CUDA graph replay.
    Python window counters are advanced explicitly because replay executes GPU
    operations only. Tail batches still use the ordinary eager metric path.
    """

    @torch.no_grad()
    def __init__(self, model, batch, metric):
        rng = torch.cuda.get_rng_state()
        self.batch = batch.map(lambda value: value.clone())
        self.metric = metric
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                out, values, valid = forward_batch(model, self.batch)
                metric.add(model, out, values, valid, self.batch)
        torch.cuda.current_stream().wait_stream(stream)
        self.graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph, stream=stream):
            out, values, valid = forward_batch(model, self.batch)
            metric.add(model, out, values, valid, self.batch)
        self.reset()
        torch.cuda.set_rng_state(rng)

    def reset(self):
        """Clear totals in place: captured addresses and live model weights stay valid."""
        metric = self.metric
        for name in ("loss", "raw", "moments", "activity_confusion", "violations",
                     "coordinate_errors", "embedding_moments", "embedding_count",
                     "denominators", "histogram", "bands", "unknown_frames", "coverage", "average_ratio", "objective_sums", "evaluated_bars"):
            getattr(metric, name).zero_()
        for table in (*metric.moves.tables.values(), *metric.moves.confusions.values()):
            table.zero_()
        metric.windows = 0

    def __call__(self, batch):
        # raw may alias grid in some batch contracts; copying it twice is safe
        # and ensures the independent original-unit targets are never stale.
        for name in ("grid", "slots", "valid", "frames", "raw"):
            getattr(self.batch, name).copy_(getattr(batch, name))
        self.graph.replay()
        self.metric.windows += len(batch.target)

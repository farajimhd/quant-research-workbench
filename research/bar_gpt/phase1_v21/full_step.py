"""One captured raw-input/teacher/student/backward path; optimizer outside."""
import torch
from ..phase1_v7.full_data import gpu_inputs
from ..phase1_v12.model import gather
from .step import backward as prepared_backward


def inputs(teacher, batch, pos):
    with torch.no_grad():
        x, v, _ = gpu_inputs(batch)
        # Only observed teacher embeddings enter the anchor objective.
        z = teacher.encode(gather(x, pos), gather(v, pos))
        old = z.new_zeros(len(x), 64, z.shape[-1]).scatter(
            1, pos[..., None].expand(-1, -1, z.shape[-1]), z)
    return x, v, batch.grid, batch.frames, pos, old


def backward(model, teacher, batch, pos, scale):
    return prepared_backward(model, inputs(teacher, batch, pos), scale)


class Replay:
    def __init__(self, model, teacher, batch, pos, scale):
        self.model = model
        self.batch = batch.map(lambda x: x.clone())
        self.pos = pos.clone()
        self.graph = torch.cuda.CUDAGraph()
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                model.zero_grad(set_to_none=True)
                backward(model, teacher, self.batch, self.pos, scale)
        torch.cuda.current_stream().wait_stream(stream)
        model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph, stream=stream):
            self.output = backward(model, teacher, self.batch, self.pos, scale)
        self.params = list(model.parameters())
        self.grads = [p.grad for p in self.params]

    def __call__(self, batch, pos):
        for name in ('grid', 'slots', 'valid', 'frames'):
            getattr(self.batch, name).copy_(getattr(batch, name))
        self.pos.copy_(pos)
        for p, g in zip(self.params, self.grads):
            p.grad = g
        self.graph.replay()
        return self.output


def qualify(model, teacher, batch, pos, scale):
    model.zero_grad(set_to_none=True)
    expected = backward(model, teacher, batch, pos, scale)
    expected = tuple(x.clone() for x in expected)
    grads = [None if p.grad is None else p.grad.clone() for p in model.parameters()]
    # Also prove that visible-only teacher work preserves the original anchor.
    x, v, raw, frames, _, _ = inputs(teacher, batch, pos)
    with torch.no_grad():
        old = teacher.encode(x, v)
    model.zero_grad(set_to_none=True)
    original = prepared_backward(model, (x, v, raw, frames, pos, old), scale)
    for a, b in zip(original, expected):
        torch.testing.assert_close(a, b, rtol=1e-4, atol=2e-5)
    replay = Replay(model, teacher, batch, pos, scale)
    actual = replay(batch, pos)
    for a, b in zip(actual, expected):
        torch.testing.assert_close(a, b, rtol=1e-4, atol=2e-5)
    for p, g in zip(model.parameters(), grads):
        if g is None:
            assert p.grad is None
        else:
            torch.testing.assert_close(p.grad, g, rtol=1e-3, atol=2e-5)
    return replay, actual

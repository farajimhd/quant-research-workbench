"""Bounded real FlexAttention forward/backward parity check before throughput comparison."""

import argparse
from pathlib import Path
import time

import torch
from torch.nn.functional import scaled_dot_product_attention

from .encoder_v1 import compiled_flex
from .shards.contract import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path("D:/TradingML/runtimes").resolve()
    if not root.is_dir() or not args.output.resolve().is_relative_to(root):
        raise ValueError("Probe outputs must stay under the runtime root.")
    args.output.mkdir(parents=True, exist_ok=True)
    import os
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(args.output / "inductor-cache")
    os.environ["TRITON_CACHE_DIR"] = str(args.output / "triton-cache")
    torch.manual_seed(17)
    results = []
    for length in (10, 11, 13, 14):
        inputs = [torch.randn(2, 4, length, 32, device="cuda", dtype=torch.bfloat16, requires_grad=True) for _ in range(3)]
        copies = [item.detach().clone().requires_grad_() for item in inputs]
        expected = scaled_dot_product_attention(*inputs)
        expected.float().square().mean().backward()
        began = time.perf_counter()
        actual = compiled_flex()(*copies)
        actual.float().square().mean().backward()
        torch.cuda.synchronize()
        torch.testing.assert_close(actual, expected, rtol=.02, atol=.02)
        for left, right in zip(copies, inputs):
            torch.testing.assert_close(left.grad, right.grad, rtol=.03, atol=1e-4)
        results.append(dict(length=length, seconds_including_compile=time.perf_counter()-began,
                            max_output_difference=float((actual-expected).abs().max()),
                            max_gradient_difference=max(float((left.grad-right.grad).abs().max()) for left, right in zip(copies, inputs))))
        write_json(args.output / "parity.json", dict(status="passed", results=results))
        print(results[-1], flush=True)


if __name__ == "__main__":
    main()

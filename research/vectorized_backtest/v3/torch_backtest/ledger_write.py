"""Explicit ledger mutation, preventing whole-ledger compiler copies.

Inductor functionalizes a traced scatter into two complete ledger copies plus
copy-back. The replay owns this buffer exclusively; declare that mutation as
an opaque Torch operation instead. Its implementation is still the native
Torch scatter on CPU/CUDA, with unchanged unique destinations and FP64 rows.
CUDA graph capture records the underlying scatter kernel, not a Python loop.
"""

import torch


def _scatter(
    ledger: torch.Tensor, destinations: torch.Tensor, rows: torch.Tensor
) -> None:
    # The caller proves uniqueness by prefix-ranking active fills and assigning
    # inactive slots private scratch rows. It detects overflow before results.
    ledger.scatter_(1, destinations[..., None].expand(-1, -1, 9), rows)


def _fake_ledger_scatter(ledger, destinations, rows):
    # Shape/state metadata is unchanged; the operation returns no alias.
    return None


# Pytest may collect the research package through both its short and qualified
# module names. Reuse a registered dispatcher operation instead of registering
# it twice (which would invalidate the first CustomOpDef's schema handle).
try:
    ledger_scatter = torch.ops.torch_backtest_v3.ledger_scatter.default
except AttributeError:
    ledger_scatter = torch.library.custom_op(
        "torch_backtest_v3::ledger_scatter", mutates_args=("ledger",)
    )(_scatter)
    ledger_scatter.register_fake(_fake_ledger_scatter)

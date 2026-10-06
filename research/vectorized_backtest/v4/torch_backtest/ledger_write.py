"""Explicit ledger mutation, preventing whole-ledger compiler copies.

Inductor functionalizes a traced scatter into two complete ledger copies plus
copy-back. The replay owns this buffer exclusively; declare that mutation as
an opaque Torch operation instead. Ranking and row construction belong to the
same mutation boundary: Torch 2.12 cannot reliably fuse their flattened prime
listing axes against an opaque scatter alone. All operations remain native
Torch on CPU/CUDA, with unchanged unique destinations and FP64 rows. CUDA
graph capture records these device kernels, not a Python loop.
"""

import torch


def _append(
    ledger: torch.Tensor,
    fill_count: torch.Tensor,
    overflow: torch.Tensor,
    qty: torch.Tensor,
    price: torch.Tensor,
    fee: torch.Tensor,
    now: torch.Tensor,
    reason: torch.Tensor,
    clock: torch.Tensor,
    slot_axis: torch.Tensor,
    side: int,
    maximum_fills: int,
) -> None:
    shape = (qty.shape[0], qty.shape[1] * qty.shape[2])
    active = qty.reshape(shape) > 0
    positions = fill_count[:, None] + active.cumsum(-1) - 1
    in_bounds = positions < maximum_fills
    overflow.logical_or_((active & ~in_bounds).any(-1))
    rows = torch.stack(
        (
            now.expand(shape),
            slot_axis.expand(shape) // 15,
            slot_axis.expand(shape) % 15,
            torch.full_like(qty.reshape(shape), side),
            qty.reshape(shape),
            price.expand(qty.shape).reshape(shape),
            fee.reshape(shape),
            reason.expand(qty.shape).reshape(shape),
            clock.expand(shape),
        ),
        -1,
    ).to(torch.float64)
    rows = torch.where((active & in_bounds)[..., None], rows, 0)
    # Active prefix ranks and inactive private scratch rows are all unique.
    # Overflow is recorded and fails closed before any completed result.
    destinations = torch.where(active & in_bounds, positions, maximum_fills + slot_axis)
    ledger.scatter_(1, destinations[..., None].expand(-1, -1, 9), rows)
    fill_count.add_(active.sum(-1))


def _fake_ledger_append(*args):
    # Shape/state metadata is unchanged; the operation returns no alias.
    return None


# Pytest may collect the research package through both its short and qualified
# module names. Reuse a registered dispatcher operation instead of registering
# it twice (which would invalidate the first CustomOpDef's schema handle).
try:
    ledger_append = torch.ops.torch_backtest_v4.ledger_append.default
except AttributeError:
    ledger_append = torch.library.custom_op(
        "torch_backtest_v4::ledger_append",
        mutates_args=("ledger", "fill_count", "overflow"),
    )(_append)
    ledger_append.register_fake(_fake_ledger_append)

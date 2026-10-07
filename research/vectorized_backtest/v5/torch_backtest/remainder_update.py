"""Isolated vectorized remainder mutation, shared by CPU and captured CUDA.

Torch2.12 cannot safely fuse the wide [B,N,15] financial updates with this
second order-state transition. Declare the owned mutation explicitly, as for
ledger writes. No Python loop over accounts, tickers or orders is executed.
CUDA uses a separately compiled mutation kernel inside this opaque boundary;
CPU retains native expressions as its oracle. No host reads or fallback.
"""
from functools import wraps

import torch

def _advance(remaining: torch.Tensor, buy_filled: torch.Tensor,
             buy_created: torch.Tensor, buy_retries: torch.Tensor,
             buy_last_retry: torch.Tensor, exit_kind: torch.Tensor,
             buy_reference: torch.Tensor, buy_limit: torch.Tensor,
             buy_paid: torch.Tensor, buy_submitted: torch.Tensor,
             buy_deadline: torch.Tensor, policy_cancelled: torch.Tensor,
             expired_shares: torch.Tensor, values: list[torch.Tensor],
             now: torch.Tensor, ask: torch.Tensor, signal_valid: torch.Tensor,
             cash: torch.Tensor, fee_cover: torch.Tensor,
             fee_per_share: float, minimum_order_fee: float) -> None:
    slots = remaining.shape[1] // ask.shape[0]
    mode = values[0]
    pending = remaining > 0
    partial = pending & (buy_filled > 0)
    age_limit = buy_created + values[1]
    signal_cancel = (values[2] == 1) & ~signal_valid.repeat_interleave(slots, dim=1)
    policy_cancel = pending & (((mode == 0) & partial) | signal_cancel)
    policy_cancelled.add_(
        torch.where(policy_cancel, remaining, 0).sum(1))
    remaining.copy_(torch.where(policy_cancel, 0, remaining))
    room = buy_retries < values[3]
    due = now - buy_last_retry >= values[4]
    retry = (partial & ~policy_cancel & room & due & (now < age_limit)
             & (exit_kind == 0)
             & ((mode == 2) | ((mode == 3) & (now >= buy_deadline))))
    valid_ask = torch.isfinite(ask).repeat_interleave(slots)[None] & (ask.repeat_interleave(slots)[None] > 0)
    retry &= (mode != 2) | valid_ask
    proposed = torch.minimum(
        ask.nan_to_num(0).repeat_interleave(slots)[None] * (1 + values[5]),
        buy_reference * (1 + values[6] / 10000))
    proposed = torch.where(retry & (mode == 2), proposed, buy_limit)
    # Price chasing cannot spend reserved cash a second time. Distribute
    # available incremental cover over all simultaneous amendments.
    extra = ((proposed - buy_limit).clamp_min(0) * remaining).sum(1)
    pending_fee = (torch.maximum(torch.full_like(buy_paid, minimum_order_fee),
                   (buy_filled + remaining) * fee_per_share)
                   - buy_paid).clamp_min(0)
    reserved = (remaining * buy_limit
                + torch.where(remaining > 0, pending_fee, 0)).sum(1)
    headroom = (cash - reserved - fee_cover).clamp_min(0)
    factor = (headroom / extra.clamp_min(1e-12)).clamp_max(1)[:, None]
    new_limit = buy_limit + (proposed - buy_limit).clamp_min(0) * factor
    new_limit = torch.where(proposed < buy_limit, proposed, new_limit)
    buy_limit.copy_(torch.where(retry, new_limit, buy_limit))
    buy_submitted.copy_(torch.where(retry, now, buy_submitted))
    buy_last_retry.copy_(torch.where(retry, now, buy_last_retry))
    buy_retries.add_(retry.to(torch.int64))
    buy_deadline.copy_(torch.where(retry, torch.minimum(
        now + values[7], age_limit), buy_deadline))
    # Resubmission can wait out its cooldown without consuming liquidity.
    waiting_retry = partial & (mode == 3) & room & ~policy_cancel & (now < age_limit)
    expired = (remaining > 0) & ((now >= age_limit)
              | ((now >= buy_deadline) & ~waiting_retry))
    expired_shares.add_(torch.where(expired, remaining, 0).sum(1))
    remaining.copy_(torch.where(expired, 0, remaining))


# Compile only this isolated mutation boundary. Keeping it opaque to the outer
# financial graph avoids the wide-axis fusion defect while reducing launches
# inside captured CUDA graphs. CPU remains the independent native oracle.
_advance_cuda = None


@wraps(_advance)
def _dispatch(*args, **kwargs):
    global _advance_cuda
    if not args[0].is_cuda:
        return _advance(*args, **kwargs)
    # Workstation launchers install the pinned Triton search path at runtime.
    # Creating a compiler at module import can cache "Triton unavailable"
    # before that setup, even though the eventual CUDA process is configured.
    if _advance_cuda is None:
        _advance_cuda = torch.compile(
            _advance, fullgraph=True, options={"comprehensive_padding": False}
        )
    return _advance_cuda(*args, **kwargs)


try:
    remainder_update = torch.ops.torch_backtest_v5.remainder_update.default
except AttributeError:
    remainder_update = torch.library.custom_op(
        "torch_backtest_v5::remainder_update",
        mutates_args=("remaining", "buy_retries", "buy_last_retry", "buy_limit",
                      "buy_submitted", "buy_deadline", "policy_cancelled", "expired_shares"),
    )(_dispatch)
    remainder_update.register_fake(lambda *args: None)

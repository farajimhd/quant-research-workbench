"""Streaming, sized autoregressive policy for dynamic Phase 3 supervision.

This defines the model contract only. A certified causal feature binder, action
mask builder, trainer, and executable replay must be supplied before use.
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F


@dataclass
class PolicyState:
    # [batch, listings, history, width], oldest second first.
    temporal: torch.Tensor
    # [batch, width], persists across orders and seconds in one session.
    actions: torch.Tensor

    def detach(self) -> 'PolicyState':
        """Bound backpropagation at a chronological training-chunk boundary."""
        return PolicyState(self.temporal.detach(), self.actions.detach())


class DynamicMarketPolicy(nn.Module):
    """Predict STOP, a listing BUY, or an indexed holding SELL plus BUY size.

    The listing and holding axes are padded per batch, not fixed portfolio caps.
    At inference the caller applies causal action masks and updates account and
    holdings after each predicted order. The size output is the fraction of
    *remaining* cash to use for a chosen BUY, in [0, 1].
    """

    def __init__(self, *, features: int, ticker_vocabulary: int,
                 history_seconds: int = 120, width: int = 128):
        super().__init__()
        if min(features, ticker_vocabulary, history_seconds, width) < 1:
            raise ValueError('Invalid dynamic policy dimensions')
        self.history_seconds = history_seconds
        self.width = width
        self.feature = nn.Linear(features, width)
        self.lag = nn.Parameter(torch.zeros(width, 1, history_seconds))
        nn.init.normal_(self.lag, std=history_seconds ** -.5)
        self.temporal_norm = nn.LayerNorm(width)
        self.identity = nn.Embedding(ticker_vocabulary + 2, width, padding_idx=0)
        self.unknown_ticker_id = ticker_vocabulary + 1
        # Full N-by-N attention is prohibitive for the market-wide population.
        # This gated set summary is O(N*D), independent of listing order.
        self.market_gate = nn.Linear(width, 1)
        self.market_mix = nn.Sequential(nn.Linear(2 * width, width), nn.GELU(),
                                        nn.Linear(width, width), nn.LayerNorm(width))
        self.account = nn.Linear(5, width)
        self.holding = nn.Linear(4, width)
        self.order_state = nn.GRUCell(width, width)
        self.action_size = nn.Linear(1, width)
        self.stop_head = nn.Linear(width, 1)
        self.buy_head = nn.Linear(width, 1)
        self.sell_head = nn.Linear(width, 1)
        self.buy_size_head = nn.Linear(width, 1)

    def initial_state(self, batch: int, listings: int, *, device: torch.device,
                      dtype: torch.dtype) -> PolicyState:
        return PolicyState(torch.zeros(batch, listings, self.history_seconds,
                                       self.width, device=device, dtype=dtype),
                           torch.zeros(batch, self.width, device=device, dtype=dtype))

    def encode_sequence(self, seconds: torch.Tensor) -> torch.Tensor:
        """Encode [B,T,N,F] once; output [B,T,N,D] with only past/current data.

        Unlike overlapping 120-second windows, each second's features are
        projected once. Chunk boundaries can be handled with ``advance``.
        """
        if seconds.ndim != 4:
            raise ValueError('Expected [batch, seconds, listings, features]')
        batch, steps, listings, _ = seconds.shape
        projected = self.feature(seconds).permute(0, 2, 3, 1)
        projected = projected.reshape(batch * listings, self.width, steps)
        encoded = F.conv1d(F.pad(projected, (self.history_seconds - 1, 0)),
                           self.lag, groups=self.width)
        encoded = encoded.reshape(batch, listings, self.width, steps)
        return self.temporal_norm(F.gelu(encoded.permute(0, 3, 1, 2)))

    def encode_chunk(self, seconds: torch.Tensor,
                     state: PolicyState) -> tuple[torch.Tensor, PolicyState]:
        """Encode [B,T,N,F] once and retain an exact 120-second stream cache.

        Returns [B,T,N,D] and the state after the chunk. Chronological chunks
        must keep a stable identity-ordered listing axis. The trainer detaches
        the returned state at bounded optimization boundaries.
        """
        if (seconds.ndim != 4 or seconds.shape[0] != state.temporal.shape[0] or
                seconds.shape[2] != state.temporal.shape[1]):
            raise ValueError('Chunk listing axis differs from streaming state')
        batch, steps, listings, _ = seconds.shape
        projected = self.feature(seconds).permute(0, 2, 1, 3)
        combined = torch.cat((state.temporal, projected), dim=2)
        convolution = F.conv1d(
            combined.permute(0, 1, 3, 2).reshape(batch * listings, self.width, -1),
            self.lag, groups=self.width)[:, :, 1:]
        encoded = convolution.reshape(batch, listings, self.width, steps)
        encoded = self.temporal_norm(F.gelu(encoded.permute(0, 3, 1, 2)))
        return encoded, PolicyState(combined[:, :, -self.history_seconds:], state.actions)

    def advance(self, second: torch.Tensor, state: PolicyState) -> tuple[torch.Tensor, PolicyState]:
        """Append [B,N,F] and return current [B,N,D] without a window copy."""
        if second.ndim != 3 or second.shape[:2] != state.temporal.shape[:2]:
            raise ValueError('Streaming listing shape changed; remap by identity before advancing')
        projected = self.feature(second)
        temporal = torch.cat((state.temporal[:, :, 1:], projected.unsqueeze(2)), dim=2)
        encoded = torch.einsum('bntd,dt->bnd', temporal, self.lag[:, 0, :])
        return self.temporal_norm(F.gelu(encoded)), PolicyState(temporal, state.actions)

    def market_context(self, encoded: torch.Tensor, ticker_id: torch.Tensor,
                       valid: torch.Tensor, account: torch.Tensor,
                       held_index: torch.Tensor, held_valid: torch.Tensor,
                       held_features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return listing [B,N,D], account context [B,D], holdings [B,H,D].

        Every active holding must resolve to a causal listing slot. This also
        makes a missing held ticker an explicit data error rather than a sell
        decision based only on stale account metadata.
        """
        # The data adapter validates nonempty listing rows and held-index
        # coverage once per session. GPU scalar checks here would synchronize
        # the device at every second of a 57,481-step rollout.
        tokens = encoded + self.identity(ticker_id)
        weights = self.market_gate(tokens).squeeze(-1).masked_fill(
            ~valid, torch.finfo(tokens.dtype).min).softmax(1)
        summary = (tokens * weights.unsqueeze(-1)).sum(1)
        listings = self.market_mix(torch.cat((tokens,
            summary.unsqueeze(1).expand_as(tokens)), dim=-1))
        listings = listings.masked_fill(~valid.unsqueeze(-1), 0)
        context = listings.sum(1) / valid.sum(1, keepdim=True).clamp_min(1)
        # Account fields are cash, marked equity, realized P&L, exposure,
        # and seconds since the last executed action.
        context = context + self.account(self._scaled_account(account))
        index = held_index.clamp(0, encoded.shape[1] - 1)
        held = torch.gather(listings, 1, index.unsqueeze(-1).expand(-1, -1, self.width))
        held = held + self.holding(held_features)
        held = held.masked_fill(~held_valid.unsqueeze(-1), 0)
        return listings, context, held

    @staticmethod
    def _scaled_account(account: torch.Tensor) -> torch.Tensor:
        return torch.cat((torch.sign(account[:, :3]) * torch.log1p(account[:, :3].abs()),
                          account[:, 3:4],
                          torch.log1p(account[:, 4:5].clamp_min(0)) / 10), dim=1)

    def order_outputs(self, listings: torch.Tensor, context: torch.Tensor,
                      held: torch.Tensor, history: torch.Tensor,
                      valid: torch.Tensor, held_valid: torch.Tensor,
                      action_mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Produce action logits [B,1+N+H] and conditional BUY sizes [B,N]."""
        focus = context + history
        buy_focus = torch.tanh(listings + focus.unsqueeze(1))
        logits = torch.cat((self.stop_head(focus),
                            self.buy_head(buy_focus).squeeze(-1),
                            self.sell_head(torch.tanh(held + focus.unsqueeze(1))).squeeze(-1)), 1)
        admissible = torch.cat((torch.ones_like(valid[:, :1]), valid, held_valid), 1)
        if action_mask.shape != logits.shape:
            raise ValueError('Action mask has the wrong shape')
        logits = logits.masked_fill(~(admissible & action_mask), torch.finfo(logits.dtype).min)
        return logits, self.buy_size_head(buy_focus).squeeze(-1)

    def remember_action(self, history: torch.Tensor, listings: torch.Tensor,
                        held: torch.Tensor, token: torch.Tensor,
                        size: torch.Tensor) -> torch.Tensor:
        """Advance action memory using a selected token and realized BUY fraction."""
        batch, listings_count, width = listings.shape
        if held.shape[1] == 0:
            held = listings.new_zeros(batch, 1, width)
        sell = token > listings_count
        buy = (token > 0) & ~sell
        buy_index = (token - 1).clamp(0, listings_count - 1)
        sell_index = (token - listings_count - 1).clamp(0, held.shape[1] - 1)
        buy_value = torch.gather(listings, 1, buy_index[:, None, None].expand(-1, 1, width)).squeeze(1)
        sell_value = torch.gather(held, 1, sell_index[:, None, None].expand(-1, 1, width)).squeeze(1)
        selected = torch.where(buy[:, None], buy_value + self.action_size(size[:, None]),
                               torch.where(sell[:, None], sell_value, torch.zeros_like(buy_value)))
        updated = self.order_state(selected, history)
        # STOP is a decision target, not a trade. Keeping state unchanged lets
        # training skip empty seconds while preserving true action history.
        return torch.where((token != 0)[:, None], updated, history)

    def teacher_forced_second(self, second: torch.Tensor, state: PolicyState, *,
                              ticker_id: torch.Tensor, valid: torch.Tensor,
                              held_index: torch.Tensor, held_valid: torch.Tensor,
                              held_features: torch.Tensor,
                              account_by_order: torch.Tensor,
                              action_mask: torch.Tensor,
                              teacher_tokens: torch.Tensor,
                              teacher_sizes: torch.Tensor,
                              order_valid: torch.Tensor,
                              ) -> tuple[torch.Tensor, torch.Tensor, PolicyState]:
        """Train one second using causal account snapshots and teacher orders.

        Inputs are [B,N,F] second, [B,O,5] account, [B,O,1+N+H]
        action mask, and [B,O] tokens/sizes/validity. STOP appears once;
        padding after STOP must have ``order_valid=False``. Returns action
        logits [B,O,1+N+H], BUY fraction logits [B,O,N], and next state.
        The caller is responsible for deriving account snapshots from the
        ordered teacher ledger and for resetting state at each session.
        ``held_features`` are causal quantity, entry basis, age, and marked
        return (normalized by the data adapter). Call ``state.detach()`` at
        bounded training-chunk boundaries; preserve it across seconds within
        the same session.
        """
        encoded, advanced = self.advance(second, state)
        batch, orders = teacher_tokens.shape
        if (account_by_order.shape != (batch, orders, 5) or
                teacher_sizes.shape != (batch, orders) or
                order_valid.shape != (batch, orders) or
                action_mask.shape[:2] != (batch, orders)):
            raise ValueError('Teacher order tensors do not share batch/order axes')
        vocabulary = 1 + second.shape[1] + held_index.shape[1]
        if action_mask.shape[2] != vocabulary:
            raise ValueError('Teacher action vocabulary shape changed')
        listings, first_context, held = self.market_context(
            encoded, ticker_id, valid, account_by_order[:, 0],
            held_index, held_valid, held_features)
        baseline_account = self.account(self._scaled_account(account_by_order[:, 0]))
        history = advanced.actions
        action_logits, size_logits = [], []
        for order in range(orders):
            current_account = self.account(self._scaled_account(account_by_order[:, order]))
            context = first_context - baseline_account + current_account
            logits, sizes = self.order_outputs(listings, context, held, history,
                                                valid, held_valid, action_mask[:, order])
            action_logits.append(logits)
            size_logits.append(sizes)
            updated = self.remember_action(history, listings, held,
                                           teacher_tokens[:, order], teacher_sizes[:, order])
            history = torch.where(order_valid[:, order, None], updated, history)
        return (torch.stack(action_logits, 1), torch.stack(size_logits, 1),
                PolicyState(advanced.temporal, history))

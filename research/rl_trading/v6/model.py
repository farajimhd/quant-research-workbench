"""Causal 120-actual-candle encoder for the revised market policy.

Training encodes each listing's stored candle sequence once. Serving updates
only listings with a newly completed candle; missing clock seconds never
advance a listing's history. Account/order decoding is a separate contract.
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F

from research.rl_trading.v6.features import (CONTEXT_CANDLES, LEVEL_NAMES,
                                             LEVELS_PER_SIDE, SCALAR_NAMES)


INPUT_WIDTH = len(SCALAR_NAMES) + 2 * LEVELS_PER_SIDE * len(LEVEL_NAMES)


@dataclass
class CandleState:
    temporal: torch.Tensor  # [N,120,D], circular, next write at cursor[N].
    cursor: torch.Tensor  # [N], int64.
    encoded: torch.Tensor  # [N,D], most recent completed candle only.
    seen: torch.Tensor  # [N], number of actual candles observed.


class ActualCandleEncoder(nn.Module):
    def __init__(self, width: int = 128, history_candles: int = CONTEXT_CANDLES):
        super().__init__()
        if width < 1 or history_candles != CONTEXT_CANDLES:
            raise ValueError('V6 requires 120 actual candles and positive width')
        self.width = width
        self.history_candles = history_candles
        self.project = nn.Linear(INPUT_WIDTH, width)
        self.lag = nn.Parameter(torch.empty(width, 1, history_candles))
        nn.init.normal_(self.lag, std=history_candles ** -.5)
        self.norm = nn.LayerNorm(width)

    @staticmethod
    def _input(scalar: torch.Tensor, levels: torch.Tensor) -> torch.Tensor:
        if (scalar.ndim != 2 or scalar.shape[1] != len(SCALAR_NAMES) or
                levels.shape != (scalar.shape[0], 2, LEVELS_PER_SIDE,
                                 len(LEVEL_NAMES))):
            raise ValueError('Expected [candles,37] and [candles,2,5,11]')
        return torch.cat((scalar, levels.flatten(1)), dim=1)

    def encode_listing(self, scalar: torch.Tensor,
                       levels: torch.Tensor) -> torch.Tensor:
        """Encode [C,37] + [C,2,5,11] once to [C,D], causal by candle.

        Depthwise temporal convolution shares a lag filter per channel. The
        left pad represents missing *actual candles*, not market-clock gaps.
        The caller provides a prior-session tail as a prefix when available.
        """
        projected = self.project(self._input(scalar, levels))
        if projected.shape[0] == 0:
            return projected
        ordered = projected.T.unsqueeze(0)  # [1,D,C].
        padded = F.pad(ordered, (self.history_candles - 1, 0))
        convolved = F.conv1d(padded, self.lag, groups=self.width)
        return self.norm(F.gelu(convolved.squeeze(0).T))

    def initial_state(self, listings: int, *, device: torch.device,
                      dtype: torch.dtype) -> CandleState:
        if listings < 1:
            raise ValueError('Market must have at least one listing')
        return CandleState(
            torch.zeros(listings, self.history_candles, self.width,
                        device=device, dtype=dtype),
            torch.zeros(listings, device=device, dtype=torch.long),
            torch.zeros(listings, self.width, device=device, dtype=dtype),
            torch.zeros(listings, device=device, dtype=torch.long))

    @torch.no_grad()
    def observe(self, state: CandleState, listing_index: torch.Tensor,
                scalar: torch.Tensor, levels: torch.Tensor) -> CandleState:
        """Update only K changed listings in a single completed-candle event.

        Serving state is mutable and inference-only. Use `encode_listing` for
        differentiable training; it yields the same per-candle embeddings.
        A listing can occur at most once in this call, preserving event order.
        """
        if (listing_index.ndim != 1 or listing_index.dtype != torch.long or
                listing_index.shape[0] != scalar.shape[0] or
                listing_index.unique().numel() != listing_index.numel() or
                (listing_index.numel() and
                 (listing_index.min() < 0 or
                  listing_index.max() >= state.temporal.shape[0]))):
            raise ValueError('Invalid or duplicate sparse listing indices')
        if listing_index.numel() == 0:
            return state
        projected = self.project(self._input(scalar, levels))  # [K,D].
        cursor = state.cursor[listing_index]
        state.temporal[listing_index, cursor] = projected
        next_cursor = (cursor + 1) % self.history_candles
        state.cursor[listing_index] = next_cursor
        state.seen[listing_index] += 1
        order = (next_cursor[:, None] +
                 torch.arange(self.history_candles,
                              device=next_cursor.device)[None, :]) % self.history_candles
        rows = state.temporal[listing_index]
        ordered = rows.gather(1, order[..., None].expand(-1, -1, self.width))
        weighted = (ordered * self.lag[:, 0, :].T[None]).sum(dim=1)
        state.encoded[listing_index] = self.norm(F.gelu(weighted))
        return state


class BracketActionDecoder(nn.Module):
    """One order at a time: HOLD, ENTER, EXIT, SET_STOP, SET_TARGET.

    The caller supplies causal account/holding state and admissibility masks.
    A newly submitted entry is not a holding until a later confirmed fill;
    consequently it cannot receive a child bracket order in the same step.
    Decoder output is a proposal only. The OMS validates tick and quote rules.
    """

    def __init__(self, width: int = 128):
        super().__init__()
        if width < 1:
            raise ValueError('Invalid action width')
        self.width = width
        self.account = nn.Linear(5, width)
        self.holding = nn.Linear(width + 4, width)
        self.hold_head = nn.Linear(width, 1)
        self.enter_head = nn.Linear(width, 1)
        self.exit_head = nn.Linear(width, 1)
        self.stop_head = nn.Linear(width, 1)
        self.target_head = nn.Linear(width, 1)
        self.size_head = nn.Linear(width, 1)
        self.stop_distance_head = nn.Linear(width, 1)
        self.target_distance_head = nn.Linear(width, 1)

    def forward(self, listings: torch.Tensor, account: torch.Tensor,
                held_index: torch.Tensor, held_features: torch.Tensor,
                *, enter_allowed: torch.Tensor, exit_allowed: torch.Tensor,
                stop_allowed: torch.Tensor, target_allowed: torch.Tensor,
                ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return logits [1+N+3H], size [N], distances [H] each.

        `listings` is [N,D]. Account is [5] (cash, equity, realized P&L,
        exposure, seconds since action). Held features [H,4] are quantity,
        cost basis, age, and marked return. STOP/target distances are raw
        learnable predictions; execution code applies quote/tick constraints.
        """
        if (listings.ndim != 2 or listings.shape[1] != self.width or
                account.shape != (5,) or held_index.ndim != 1 or
                held_index.dtype != torch.long or
                held_features.shape != (len(held_index), 4) or
                enter_allowed.shape != (len(listings),) or
                any(mask.shape != (len(held_index),) for mask in
                    (exit_allowed, stop_allowed, target_allowed)) or
                (len(held_index) and
                 (held_index.min() < 0 or held_index.max() >= len(listings)))):
            raise ValueError('Invalid market action axes or holding identity')
        if not all(mask.dtype == torch.bool for mask in
                   (enter_allowed, exit_allowed, stop_allowed, target_allowed)):
            raise ValueError('Action masks must be boolean')
        # [D] pooled market state plus a causal account projection.
        context = listings.mean(dim=0) + self.account(account)
        listed = torch.tanh(listings + context[None])  # [N,D].
        held = torch.tanh(self.holding(torch.cat(
            (listings[held_index], held_features), dim=1)) + context[None])
        logits = torch.cat((self.hold_head(context).view(1),
            self.enter_head(listed).flatten(),
            self.exit_head(held).flatten(),
            self.stop_head(held).flatten(),
            self.target_head(held).flatten()))
        mask = torch.cat((torch.ones(1, dtype=torch.bool,
                                      device=listings.device), enter_allowed,
                          exit_allowed, stop_allowed, target_allowed))
        logits = logits.masked_fill(~mask, torch.finfo(logits.dtype).min)
        size = self.size_head(listed).flatten().sigmoid()
        stop_distance = self.stop_distance_head(held).flatten()
        target_distance = self.target_distance_head(held).flatten()
        return logits, size, stop_distance, target_distance

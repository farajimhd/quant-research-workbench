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
HELD_FEATURE_WIDTH = 11  # Nine price/action fields plus modeled pause flag/age.


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
        self.account = nn.Sequential(nn.Linear(7, width), nn.LayerNorm(width))
        self.holding = nn.Sequential(
            nn.Linear(width + HELD_FEATURE_WIDTH, width), nn.LayerNorm(width))
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

        `listings` is [N,D]. Account is [7] (cash, equity, realized P&L,
        exposure, seconds since action, reserved cash, pending entry count).
        Held features [H,9] are quantity,
        cost basis, age, marked return, stop/target distances from the causal
        mark, stop/target armed flags, and stop pending. Raw prediction heads
        are converted to valid prices by the OMS adapter.
        """
        # Price-action teacher artifacts deliberately contain only their nine
        # causal fields. Adapt in memory; do not rewrite audited teacher data.
        if held_features.shape == (len(held_index), 9):
            held_features = torch.nn.functional.pad(held_features, (0, 2))
        if (listings.ndim != 2 or listings.shape[1] != self.width or
                account.shape != (7,) or held_index.ndim != 1 or
                held_index.dtype != torch.long or
                held_features.shape != (len(held_index), HELD_FEATURE_WIDTH) or
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
        account_scaled = torch.cat((
            torch.sign(account[:3]) * torch.log1p(account[:3].abs()),
            account[3:4],
            torch.log1p(account[4:5].clamp_min(0)) / 10,
            torch.log1p(account[5:7].clamp_min(0))))
        held_scaled = held_features.clone()
        if len(held_index):
            held_scaled[:, :2] = torch.log1p(held_features[:, :2].clamp_min(0))
            held_scaled[:, 2] = torch.log1p(held_features[:, 2].clamp_min(0)) / 10
        context = listings.mean(dim=0) + self.account(account_scaled)
        listed = torch.tanh(listings + context[None])  # [N,D].
        held = torch.tanh(self.holding(torch.cat(
            (listings[held_index], held_scaled), dim=1)) + context[None])
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

    def forward_batch(self, listings, account, held_index, held_features, *,
                      enter_allowed, exit_allowed, stop_allowed, target_allowed):
        """Decode independent causal observations [B,N,D], with equal H per batch.

        Only the leading observation axis is batched. No reduction mixes
        observations, time, account state, or holding identities.
        """
        b, n, d = listings.shape
        h = held_index.shape[1]
        if held_features.shape == (b, h, 9):
            held_features = F.pad(held_features, (0, 2))
        if (d != self.width or account.shape != (b, 7) or
                held_index.shape != (b, h) or held_index.dtype != torch.long or
                held_features.shape != (b, h, HELD_FEATURE_WIDTH) or
                enter_allowed.shape != (b, n) or
                any(m.shape != (b, h) for m in (exit_allowed, stop_allowed, target_allowed)) or
                (h and (held_index.min() < 0 or held_index.max() >= n))):
            raise ValueError('Invalid batched market action axes')
        if not all(m.dtype == torch.bool for m in
                   (enter_allowed, exit_allowed, stop_allowed, target_allowed)):
            raise ValueError('Action masks must be boolean')
        scaled = torch.cat((account[:, :3].sign()*torch.log1p(account[:, :3].abs()),
            account[:, 3:4], torch.log1p(account[:, 4:5].clamp_min(0))/10,
            torch.log1p(account[:, 5:7].clamp_min(0))), dim=1)
        held_scaled = held_features.clone()
        held_scaled[:, :, :2] = torch.log1p(held_features[:, :, :2].clamp_min(0))
        held_scaled[:, :, 2] = torch.log1p(held_features[:, :, 2].clamp_min(0))/10
        context = listings.mean(dim=1) + self.account(scaled)  # [B,D]
        listed = torch.tanh(listings + context[:, None])  # [B,N,D]
        identities = held_index[:, :, None].expand(b, h, d)
        held = torch.tanh(self.holding(torch.cat((listings.gather(1, identities),
            held_scaled), dim=2)) + context[:, None])  # [B,H,D]
        logits = torch.cat((self.hold_head(context), self.enter_head(listed).squeeze(-1),
            self.exit_head(held).squeeze(-1), self.stop_head(held).squeeze(-1),
            self.target_head(held).squeeze(-1)), dim=1)
        mask = torch.cat((torch.ones(b, 1, device=listings.device, dtype=torch.bool),
            enter_allowed, exit_allowed, stop_allowed, target_allowed), dim=1)
        logits = logits.masked_fill(~mask, torch.finfo(logits.dtype).min)
        return (logits, self.size_head(listed).squeeze(-1).sigmoid(),
                self.stop_distance_head(held).squeeze(-1), self.target_distance_head(held).squeeze(-1))


@dataclass(frozen=True)
class ExecutedActionState:
    """Cross-second history of orders and their observed execution outcomes."""

    memory: torch.Tensor  # [D], reset at each session boundary.

    def detach(self) -> 'ExecutedActionState':
        return ExecutedActionState(self.memory.detach())


class BracketPolicy(nn.Module):
    """Join the actual-candle encoder, five-action decoder, and action GRU.

    The encoder changes only on persisted completed candles. The GRU changes
    only when an order receives an execution outcome; proposals alone do not
    manufacture a holding. Serving and teacher-forced training must use the
    same ordered execution events and reset the GRU for each session.
    """

    ACTION_COUNT = 5  # HOLD, ENTER_LONG, EXIT_LONG, SET_STOP, SET_TARGET.

    def __init__(self, width: int = 128):
        super().__init__()
        self.encoder = ActualCandleEncoder(width=width)
        self.decoder = BracketActionDecoder(width=width)
        self.action_type = nn.Embedding(self.ACTION_COUNT, width)
        # Selected listing embedding, action embedding, requested cash
        # fraction, filled fraction, and realized net P&L divided by equity.
        self.action_gru = nn.GRUCell(2 * width + 3, width)

    def initial_action_state(self, *, device: torch.device,
                             dtype: torch.dtype) -> ExecutedActionState:
        return ExecutedActionState(torch.zeros(self.encoder.width, device=device,
                                               dtype=dtype))

    def decide(self, listing_embeddings: torch.Tensor, account: torch.Tensor,
               held_index: torch.Tensor, held_features: torch.Tensor,
               action_state: ExecutedActionState, *,
               enter_allowed: torch.Tensor, exit_allowed: torch.Tensor,
               stop_allowed: torch.Tensor, target_allowed: torch.Tensor,
               ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        if action_state.memory.shape != (self.encoder.width,):
            raise ValueError('Action memory width differs from V6 policy')
        # Action memory augments the causal account state. It cannot change
        # admissibility masks or create positions before a confirmed fill.
        return self.decoder(
            listing_embeddings + action_state.memory[None], account,
            held_index, held_features, enter_allowed=enter_allowed,
            exit_allowed=exit_allowed, stop_allowed=stop_allowed,
            target_allowed=target_allowed)

    def remember_execution(self, state: ExecutedActionState,
                           listing_embedding: torch.Tensor, *, action: int,
                           requested_fraction: torch.Tensor,
                           filled_fraction: torch.Tensor,
                           realized_net_over_equity: torch.Tensor,
                           ) -> ExecutedActionState:
        """Remember one observed order outcome, including an unfilled order.

        HOLD is not an order and leaves memory unchanged. Fractions are
        measured against the actual account, rather than teacher intentions.
        """
        if (action not in range(self.ACTION_COUNT) or
                listing_embedding.shape != (self.encoder.width,) or
                any(value.ndim != 0 for value in
                    (requested_fraction, filled_fraction,
                     realized_net_over_equity))):
            raise ValueError('Invalid executed-action memory input')
        if action == 0:
            return state
        token = self.action_type(torch.tensor(action,
            dtype=torch.long, device=listing_embedding.device))
        values = torch.stack((requested_fraction, filled_fraction,
                              realized_net_over_equity))
        update = torch.cat((listing_embedding, token, values))
        return ExecutedActionState(self.action_gru(update, state.memory))

    def remember_sequence(self,state,embeddings,actions,requested,filled,net):
        """Fused ordered GRU sequence using the existing GRUCell parameters.

        Inputs [K,D], [K], [K], [K], [K] are actual execution outcomes,
        already sorted by bucket and identity. Output remains [D]. No event
        pooling or changed architecture; the temporary GRU shares Parameters
        and is deliberately not registered as a second checkpoint authority.
        """
        if embeddings.shape[0]==0:
            return state
        sequence=getattr(self,'_execution_sequence',None)
        if sequence is None:
            sequence=nn.GRU(self.action_gru.input_size,self.action_gru.hidden_size)
            sequence.weight_ih_l0=self.action_gru.weight_ih
            sequence.weight_hh_l0=self.action_gru.weight_hh
            sequence.bias_ih_l0=self.action_gru.bias_ih
            sequence.bias_hh_l0=self.action_gru.bias_hh
            object.__setattr__(self,'_execution_sequence',sequence)
        values=torch.stack((requested,filled,net),dim=1).to(embeddings.dtype)
        inputs=torch.cat((embeddings,self.action_type(actions),values),dim=1)
        # cuDNN TF32 RNN gradients differ materially from the reference FP32
        # GRUCell on Blackwell. Keep precision equal before claiming parity.
        with torch.backends.cudnn.flags(allow_tf32=False):
            _,last=sequence(inputs[:,None,:],state.memory[None,None,:])
        return ExecutedActionState(last[0,0])

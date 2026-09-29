"""Causal multi-order V5 planning with next-second execution feedback.

Within a completed second, provisional positions reserve only known marked
prices and cash so subsequent orders can be sized sequentially. All orders
then meet the next observed bar. The persistent action GRU is updated from
actual fills, rather than from provisional orders that might not fill.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path

import numpy as np
import torch

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.features import FEATURE_NAMES
from research.rl_trading.v1.model_v5 import DynamicMarketPolicy, PolicyState
from research.rl_trading.v1.v5_feature_binding import FeatureBinding, feature_chunks
from research.rl_trading.v1.v5_replay import Intent, Position, ReplayAccount, ReplayGrid
from research.rl_trading.v1.v5_execution_binding import VERSION as EXECUTION_VERSION
from research.rl_trading.v2.config import Config, share_cap
from research.rl_trading.v2.fees import charges
from src.market_engine.level_book_store import read


@dataclass(frozen=True)
class PlannedSecond:
    intents: tuple[Intent, ...]
    stopped: bool


def _view(account: ReplayAccount, positions: dict[int, Position], cash: float,
          realized: float, second: int, last_action: int,
          device: torch.device):
    """Return [1,5] account and [1,H]/[1,H,4] ticker-ordered holdings."""
    listings = sorted(positions)
    width = max(1, len(listings))
    indices = np.zeros((1, width), dtype=np.int64)
    valid = np.zeros((1, width), dtype=np.bool_)
    held = np.zeros((1, width, 4), dtype=np.float32)
    exposure = 0.
    for offset, listing in enumerate(listings):
        position = positions[listing]
        price = float(account.grid.close[listing, second])
        if price <= 0 or position.entry_price <= 0 or position.shares <= 0:
            raise ValueError('Model holding has no causal positive mark')
        indices[0, offset] = listing
        valid[0, offset] = True
        held[0, offset] = (math.log1p(position.shares),
            math.log(position.entry_price),
            math.log1p(max(0, second-position.entry_second)) / math.log1p(57_600),
            price/position.entry_price-1)
        exposure += position.shares*price
    equity = cash+exposure
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError('Nonpositive causal model equity')
    state = np.asarray([[cash, equity, realized, exposure/equity,
                         second-last_action if last_action >= 0 else second]],
                       dtype=np.float32)
    return (torch.from_numpy(state).to(device),
            torch.from_numpy(indices).to(device),
            torch.from_numpy(valid).to(device),
            torch.from_numpy(held).to(device), listings)


def _held_embeddings(model, listings, indices, valid, features):
    held = torch.gather(listings, 1,
        indices.unsqueeze(-1).expand(-1, -1, listings.shape[-1]))
    return (held+model.holding(features)).masked_fill(~valid.unsqueeze(-1), 0)


@torch.no_grad()
def plan_second(model: DynamicMarketPolicy, account: ReplayAccount,
                encoded: torch.Tensor, ticker_ids: torch.Tensor,
                available: torch.Tensor, history: torch.Tensor,
                *, last_action_second: int = -1) -> tuple[PlannedSecond, torch.Tensor]:
    """Predict ordered BUY/SELL/STOP from one completed market second.

    ``encoded`` is [1,N,D] and ``available`` is the causal feature-bank
    availability mask [1,N]. Neither contains next-open/arrival volume. The
    returned listing embeddings are reused to remember actual fills.
    """
    n = len(account.grid.tickers)
    if (encoded.shape != (1, n, model.width) or ticker_ids.shape != (1, n) or
            available.shape != (1, n) or history.shape != (1, model.width) or
            account.second >= account.grid.seconds-1):
        raise ValueError('Invalid V5 policy replay axes or clock')
    device = encoded.device
    second = account.second
    virtual = {k:replace(v) for k, v in account.positions.items()}
    virtual_cash = account.cash
    realized = sum(row['net_pnl'] for row in account.position_ledger)
    first, indices, held_valid, held_features, _ = _view(
        account, virtual, virtual_cash, realized, second, last_action_second, device)
    listings, context, _ = model.market_context(encoded, ticker_ids, available,
        first, indices, held_valid, held_features)
    base_context = context-model.account(model._scaled_account(first))
    provisional = history
    intents = []
    blocked_buys = set()
    buying_started = False
    # A sell removes one initial holding; a buy permanently blocks that
    # listing for this decision second. This is a natural finite bound, not a
    # fixed portfolio or per-second order cap.
    for _ in range(len(account.positions)+n+1):
        state, indices, held_valid, held_features, held_list = _view(
            account, virtual, virtual_cash, realized, second,
            second if intents else last_action_second, device)
        context = base_context+model.account(model._scaled_account(state))
        held = _held_embeddings(model, listings, indices, held_valid, held_features)
        eligible = available.clone()
        for listing in virtual.keys() | blocked_buys:
            eligible[0, listing] = False
        # The certified grid is a read-only NumPy memmap. Copy the one-second
        # price vector before passing it to PyTorch, which otherwise warns
        # that writes through the tensor would have undefined behavior.
        price = torch.tensor(account.grid.close[:, second], device=device)
        eligible &= (price > 0) & (price <= virtual_cash)
        action_mask = torch.cat((torch.ones(1, 1, dtype=torch.bool, device=device),
            eligible, held_valid & (not buying_started)), dim=1)
        logits, sizes = model.order_outputs(listings, context, held, provisional,
                                             available, held_valid, action_mask)
        token = int(logits.argmax(dim=1).item())
        if token == 0:
            return PlannedSecond(tuple(intents), True), listings
        if token <= n:
            listing = token-1
            fraction = float(torch.sigmoid(sizes[0, listing]).item())
            if not 0 <= fraction <= 1 or not math.isfinite(fraction):
                raise ValueError('Nonfinite V5 predicted cash fraction')
            intents.append(Intent(listing, 1, fraction))
            blocked_buys.add(listing)
            buying_started = True
            price_at_decision = float(account.grid.close[listing, second])
            budget = virtual_cash*fraction
            quantity = min(share_cap(price_at_decision),
                           math.floor(budget/price_at_decision))
            if quantity > 0:
                fee = sum(charges(quantity, price_at_decision, 1,
                                  account.config).values())
                while quantity and quantity*price_at_decision+fee > budget+1e-9:
                    quantity -= 1
                    fee = sum(charges(quantity, price_at_decision, 1,
                                      account.config).values())
                if quantity:
                    virtual_cash -= quantity*price_at_decision+fee
                    virtual[listing] = Position(listing, quantity,
                        price_at_decision, fee, second)
            provisional = model.remember_action(provisional, listings, held,
                torch.tensor([token], device=device),
                torch.tensor([fraction], device=device))
        else:
            listing = held_list[token-n-1]
            position = virtual.pop(listing)
            price_at_decision = float(account.grid.close[listing, second])
            fee = sum(charges(position.shares, price_at_decision, -1,
                              account.config).values())
            virtual_cash += position.shares*price_at_decision-fee
            realized += (position.shares*(price_at_decision-position.entry_price)
                         -position.entry_fee_remaining-fee)
            intents.append(Intent(listing, -1))
            provisional = model.remember_action(provisional, listings, held,
                torch.tensor([token], device=device),
                torch.zeros(1, device=device))
    raise ValueError('V5 policy did not STOP after exhausting causal actions')


@torch.no_grad()
def execute_and_remember(model: DynamicMarketPolicy, account: ReplayAccount,
                         planned: PlannedSecond, listings: torch.Tensor,
                         history: torch.Tensor) -> tuple[torch.Tensor, int]:
    """Execute next-second intents, then retain only actual filled actions."""
    decision = account.second
    shadow = {k:replace(v) for k, v in account.positions.items()}
    before = len(account.order_trace)
    account.advance(list(planned.intents))
    traces = account.order_trace[before:]
    if len(traces) != len(planned.intents):
        raise ValueError('Replay order trace lost an intended order')
    updated = history
    last_fill = -1
    n = len(account.grid.tickers)
    device = listings.device
    for intent, trace in zip(planned.intents, traces):
        filled = int(trace['filled_shares'])
        if filled == 0:
            continue
        _, indices, valid, features, held_list = _view(account, shadow,
            float(trace['cash_before']), 0., decision, -1, device)
        held = _held_embeddings(model, listings, indices, valid, features)
        if intent.side == 1:
            token = 1+intent.listing
            spent = filled*float(trace['fill_price'])+float(trace['fee'])
            size = spent/float(trace['cash_before'])
            shadow[intent.listing] = Position(intent.listing, filled,
                float(trace['fill_price']), float(trace['fee']), account.second)
        else:
            token = 1+n+held_list.index(intent.listing)
            size = 0.
            shadow[intent.listing].shares -= filled
            if shadow[intent.listing].shares == 0:
                del shadow[intent.listing]
        updated = model.remember_action(updated, listings, held,
            torch.tensor([token], device=device),
            torch.tensor([size], device=device))
        last_fill = account.second
    if set(shadow) != set(account.positions) or any(
            shadow[k].shares != account.positions[k].shares for k in shadow):
        raise ValueError('Actual V5 action memory diverged from replay holdings')
    return updated, last_fill


def load_execution_grid(root: Path, binding: FeatureBinding) -> ReplayGrid:
    """Verify hash-bound execution-only arrays against the causal feature IDs."""
    root = Path(root).resolve()
    plan = read(root / 'plan.json')
    complete = read(root / 'complete.json')
    if (plan.get('version') != EXECUTION_VERSION or
            plan.get('date') != binding.date or
            tuple(plan.get('tickers', ())) != binding.tickers or
            plan.get('feature_bank_hash') != binding.features_hash or
            plan.get('supervision_plan_hash') != binding.supervision_plan_hash or
            plan.get('plan_hash') != digest({k:v for k,v in plan.items()
                                            if k != 'plan_hash'}) or
            complete.get('plan_hash') != plan['plan_hash'] or
            complete.get('listings') != len(binding.tickers) or
            any(file_hash(root / name) != expected for name, expected
                in complete['files'].items())):
        raise ValueError('V5 execution grid is incomplete or differs from features')
    arrays = {name:np.load(root / f'{name}.npy', mmap_mode='r', allow_pickle=False)
              for name in ('close','next_open','volume','fresh',
                           'prior_close','estimated_reference')}
    return ReplayGrid(binding.tickers, **arrays)


@torch.no_grad()
def rollout_session(model: DynamicMarketPolicy, binding: FeatureBinding,
                    execution_root: Path, ticker_ids: np.ndarray,
                    config: Config, *, device: torch.device,
                    seconds_per_chunk: int = 8) -> ReplayAccount:
    """Run every decision second, then attempt transparent terminal exits.

    The final two minutes after the teacher cutoff are execution-only. Any
    unresolved holding remains marked and makes ``valid_terminal`` false; it
    is not silently sold at an unavailable price.
    """
    if (ticker_ids.shape != (len(binding.tickers),) or
            np.any(ticker_ids < 1) or
            np.any(ticker_ids > model.unknown_ticker_id) or
            not 1 <= binding.decision_seconds < 57_601):
        raise ValueError('Invalid V5 replay identity, vocabulary, or cutoff')
    grid = load_execution_grid(execution_root, binding)
    if binding.decision_seconds >= grid.seconds:
        raise ValueError('V5 replay cutoff leaves no next-second arrival bar')
    account = ReplayAccount(grid, config)
    ids = torch.from_numpy(np.asarray(ticker_ids, dtype=np.int64).copy())[
        None].to(device)
    state = model.initial_state(1, len(binding.tickers), device=device,
                                dtype=torch.float32)
    model.eval()
    last_action = -1
    available_index = FEATURE_NAMES.index('price_available')
    for start, chunk in feature_chunks(binding,
                                       seconds_per_chunk=seconds_per_chunk,
                                       device=device):
        encoded, temporal = model.encode_chunk(chunk, state)
        history = state.actions
        for offset in range(chunk.shape[1]):
            second = start+offset
            if account.second != second:
                raise ValueError('V5 replay decision and execution clocks diverged')
            available = chunk[:, offset, :, available_index] > .5
            planned, listings = plan_second(model, account,
                encoded[:, offset], ids, available, history,
                last_action_second=last_action)
            history, filled_at = execute_and_remember(model, account,
                planned, listings, history)
            if filled_at >= 0:
                last_action = filled_at
        state = PolicyState(temporal.temporal, history)
    if account.second != binding.decision_seconds:
        raise ValueError('V5 replay did not cover every teacher decision second')
    while account.second < grid.seconds-1:
        account.advance([Intent(listing, -1, kind='terminal')
                         for listing in sorted(account.positions)])
    return account

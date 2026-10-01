"""Tensor counterparts of Strategy 1's pure entry and protection reducers.

This is a new, explicit policy ABI. Ranked V6 slots cannot stand in for the
stable identities required here. The broker/Portfolio/OMS owns financial facts
and acknowledges proposals; these functions never assume a proposal was filled.
All scalar lanes have shape [B,N]; geometry/event lanes have shape [B,N,K].
Identity indices are lossless, per-listing encodings of producer-owned strings.
Zero is a sentinel, and the declared identity envelope must never truncate IDs.
"""

from dataclasses import dataclass
from enum import IntEnum

import torch

from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError


class StatefulOperation(IntEnum):
    """Policy blocks requiring typed state, beyond the scalar expression ABI."""

    ENTRY_ADMISSION = 100
    DISTINCT_RESISTANCE_BREAKS = 101
    ORDINAL_TARGET = 102
    UPWARD_STOP = 103
    PROTECTION_CONFIRMATION = 104
    ADD_ADMISSION = 105


# [operation class, input-contract class, output-contract class, parameter set].
# These labels identify typed tensor contracts, rather than hidden callbacks.
STRATEGY_ONE_ARRAY = (
    (100, 1, 2, 0),
    (101, 3, 4, 0),
    (102, 5, 6, 0),
    (103, 7, 8, 0),
    (104, 9, 10, 0),
    (105, 11, 12, 0),
)


def validate_strategy_one_array(array):
    """Validate this prototype's declared reducer-block array.

    This does not certify a full Strategy 1 port or an executable search
    program. The audit lists the missing input/financial/broker integration.
    A searched behavioral variant also needs its own research identity.
    """
    rows = array.tolist() if hasattr(array, "tolist") else array
    if tuple(tuple(row) for row in rows) != STRATEGY_ONE_ARRAY:
        raise EncodingError("Strategy 1 requires its complete declared block array")


def below(value, tick):
    """Exact ceil-minus-one grid rule, including on-grid anchors."""
    raw = (torch.ceil(value / tick - 1e-9) - 1) * tick
    return torch.round(raw * 1e10) / 1e10


def entry_admission(x):
    """Tensor equivalent of propose_strategy_one_entry for validated facts.

    Source-side candidate/MACD/BOS certification remains a separate required
    authority. This reducer consumes its completed facts and post-broker
    financial view. It returns permission to propose, never an order or fill.
    Each mapping member is one atomic [B,N] tensor; unknown evidence is masked.
    Status permission is preencoded from the app's exact enum/permission pair.
    """
    at, episode = x["boundary_ms"], x["episode_start_ms"]
    fresh = (
        (x["bid_int"] > 0)
        & (x["ask_int"] >= x["bid_int"])
        & (x["quote_age_us"] >= 0)
        & (x["quote_age_us"] <= 1_000_000)
    )
    bos = x["bos_break_boundary_ms"]
    reentry_required = x["completed_entries"] > 0
    closed = x["closed_boundary_ms"]
    witness = (
        (closed > 0)
        & (closed < at)
        & (closed.remainder(100) == 0)
        & (x["prior_entry_resistance_id"] > 0)
        & (x["prior_position_high_int"] > 0)
        & (x["previous_bar_close_int"] > 0)
        & (x["current_bar_close_int"] > 0)
    )
    crossing_required = ((at - closed) < 10_000) | (
        x["prior_entry_resistance_id"] == x["bos_support_level_id"]
    )
    crossed = (x["previous_bar_close_int"] <= x["prior_position_high_int"]) & (
        x["prior_position_high_int"] < x["current_bar_close_int"]
    )
    return (
        x["candidate_valid"]
        & (at > 0)
        & (at <= 57_600_000)
        & (at.remainder(100) == 0)
        & (episode > 0)
        & (episode <= at)
        & (episode.remainder(100) == 0)
        & ((at - episode) <= 300_000)
        & (x["quantity"] == 0)
        & ~x["pending_entry"]
        & ~x["pending_exit"]
        & ~x["pending_capital_request"]
        & x["entry_permission"]
        & (at >= x["reentry_not_before_ms"])
        & (~reentry_required | (witness & (~crossing_required | crossed)))
        & torch.isfinite(x["activation_gap"])
        & (x["activation_gap"] > 0)
        & (bos > 0)
        & (bos <= at)
        & (bos.remainder(1000) == 0)
        & (x["bos_support_level_id"] > 0)
        & x["protection_valid"]
        & (x["target_level_id"] > 0)
        & (x["target_ordinal"] == 3)
        & torch.isfinite(x["stop"])
        & torch.isfinite(x["target"])
        & fresh
        & (x["stop"] > 0)
        & (x["stop"] < x["bid_int"] / 10000)
        & (x["ask_int"] / 10000 < x["target"])
    )


@dataclass
class ProtectionRegisters:
    """Position-owned state; [B,N] scalars and [B,N,D] accepted identities.

    D is the complete identity vocabulary size plus a sentinel. A new filled
    position initializes these registers; a reentry cannot inherit its old set.
    Group minima retain the lower band of each accepted event at its own clock,
    rather than rereading later geometry for the same identity.
    """

    boundary_ms: torch.Tensor
    stop: torch.Tensor
    target: torch.Tensor
    accepted: torch.Tensor
    pending_count: torch.Tensor
    pending_min: torch.Tensor
    earned_min: torch.Tensor
    earned_groups: torch.Tensor
    applied_groups: torch.Tensor

    @classmethod
    def empty(cls, boundary_ms, stop, target, *, identities):
        shape, device = stop.shape, stop.device
        zeros = torch.zeros(shape, dtype=torch.int64, device=device)
        infinity = torch.full_like(stop, float("inf"))
        return cls(
            boundary_ms.clone(),
            stop.clone(),
            target.clone(),
            torch.zeros((*shape, identities), dtype=torch.bool, device=device),
            zeros.clone(),
            infinity.clone(),
            infinity.clone(),
            zeros.clone(),
            zeros.clone(),
        )


@dataclass
class ResistanceRegisters:
    """Ticker-owned acceptance episode, separate from position break history.

    Geometry lanes [B,N,D] are keyed by stable identity, not nearest-price rank.
    An identity can be reaccepted after a recross; protection still counts it
    only once per held position. A quote-only second does not update this state.
    """

    boundary_ms: torch.Tensor
    close_int: torch.Tensor
    known: torch.Tensor
    lower: torch.Tensor
    upper: torch.Tensor
    accepted: torch.Tensor


def observe_resistance(state, x):
    """Vectorize observe_completed_resistance_second over stable ID columns.

    The caller supplies current producer geometry and completed price-bearing
    1s candles. Returns the next episode state and a [B,N,D] new-break mask.
    Newly visible or moved geometry cannot create a retrospective crossing.
    Missing seconds break continuity; they are not forward-filled as candles.
    """
    known = x["present"] & (x["eligible_resistance"] | state.known)
    prior_mid = (state.lower + state.upper) / 2
    close = x["close_int"][..., None]
    opened = x["open_int"][..., None]
    retained = state.accepted & ~(state.known & (close <= prior_mid * 10000))
    contiguous = (x["boundary_ms"] == state.boundary_ms + 1000) & known.any(-1)
    retained = retained & contiguous[..., None]
    same = (x["lower"] == state.lower) & (x["upper"] == state.upper)
    new = (
        contiguous[..., None]
        & (close > opened)
        & state.known
        & known
        & same
        & ~retained
        & (prior_mid * 10000 < close)
        & (
            (state.close_int[..., None] <= prior_mid * 10000)
            | (opened <= prior_mid * 10000)
        )
    )
    return ResistanceRegisters(
        x["boundary_ms"], x["close_int"], known, x["lower"], x["upper"], retained | new
    ), new


def advance_protection(state, x):
    """Propose the app's ordered target-then-stop transition on device.

    `break_id/lower` are producer-certified eligible events sorted by
    (completed boundary, midpoint, lexical identity), padded with identity 0.
    `overhead_midpoint/id` are resistance rows sorted by (midpoint, lexical ID).
    A prefix scan assigns new events to ordered triples; masked reductions
    retain their minima. There is no per-level Python loop or tensor-to-host
    scalar read. Inputs are causally validated before entering the hot path.
    """
    identity, lower = x["break_id"], x["break_lower"]
    k = identity.shape[-1]
    # First occurrence by stable ID. A repeated witness in this same batch
    # cannot earn another purchase or resistance group.
    position = torch.arange(k, device=identity.device).expand_as(identity)
    first = torch.full_like(state.accepted, k, dtype=torch.int64)
    first.scatter_reduce_(-1, identity, position, reduce="amin", include_self=True)
    new = (
        (identity > 0)
        & ~state.accepted.gather(-1, identity)
        & (position == first.gather(-1, identity))
    )
    accepted = state.accepted.to(torch.int64)
    accepted.scatter_reduce_(
        -1, identity, new.to(torch.int64), reduce="amax", include_self=True
    )
    accepted = accepted.to(torch.bool)
    rank = new.to(torch.int64).cumsum(-1)
    total = state.pending_count + new.sum(-1)
    completed = total // 3
    count = total.remainder(3)
    # Groups are zero-based relative to the unfinished triple at this call.
    group = (state.pending_count[..., None] + rank - 1) // 3
    last_earned = torch.where(
        new & (group == completed[..., None] - 1), lower, float("inf")
    ).amin(-1)
    last_earned = torch.where(
        completed == 1, torch.minimum(last_earned, state.pending_min), last_earned
    )
    earned_min = torch.where(completed > 0, last_earned, state.earned_min)
    pending = torch.where(
        new & (group == completed[..., None]), lower, float("inf")
    ).amin(-1)
    pending = torch.where(
        completed == 0, torch.minimum(pending, state.pending_min), pending
    )
    minimum = torch.where(count > 0, pending, float("inf"))
    earned = state.earned_groups + completed

    breaks = accepted.sum(-1)
    ordinal = torch.where(breaks <= 3, 3, torch.where(breaks <= 5, 2, 1))
    midpoint, identity = x["overhead_midpoint"], x["overhead_id"]
    tick = x["tick"]
    price = torch.floor(midpoint / tick[..., None] + 0.5 + 1e-9) * tick[..., None]
    price = torch.round(price * 1e10) / 1e10
    visible = (identity > 0) & torch.isfinite(price) & (price > x["ask"][..., None])
    ranked = visible.to(torch.int64).cumsum(-1)
    selected = visible & (ranked == ordinal[..., None])
    proposed_target = torch.where(selected, price, -float("inf")).amax(-1)
    target_changed = x["price_bearing_bar"] & (proposed_target > state.target)
    target = torch.where(target_changed, proposed_target, state.target)

    # Resistance wins only when executable. A rejected resistance proposal
    # must not suppress an otherwise executable completed-30s-low proposal.
    limit = torch.minimum(x["bid"], target)
    resistance = below(earned_min, tick)
    use_resistance = (
        (earned > state.applied_groups)
        & (resistance > state.stop)
        & (resistance < limit)
    )
    swing = below(x["low_int"] / 10000, tick)
    low_clock = x["low_boundary_ms"]
    valid_low = (
        (low_clock > 0)
        & (low_clock <= x["boundary_ms"])
        & ((x["boundary_ms"] - low_clock) < 30_000)
        & (low_clock.remainder(30_000) == 0)
        & x["low_valid"]
        & (x["low_int"] > 0)
    )
    use_swing = valid_low & (swing > state.stop) & (swing < limit)
    stop_changed = use_resistance | use_swing
    stop = torch.where(
        use_resistance, resistance, torch.where(use_swing, swing, state.stop)
    )
    proposed = ProtectionRegisters(
        x["boundary_ms"].clone(),
        stop,
        target,
        accepted,
        count,
        minimum,
        earned_min,
        earned,
        torch.where(use_resistance, earned, state.applied_groups),
    )
    return proposed, stop_changed, target_changed, use_resistance


def confirm_protection(previous, proposed, stop_confirmed, target_confirmed):
    """Commit broker acknowledgements; retain earned groups after a refusal.

    Target confirmation precedes stop confirmation in the caller. The transport
    must reject an acknowledgement that would cross the still-working bracket.
    This pure hot-path reducer does not turn an attempted amendment into a fill.
    """
    stop = torch.where(stop_confirmed, proposed.stop, previous.stop)
    target = torch.where(target_confirmed, proposed.target, previous.target)
    # A GPU-side assertion preserves the app's fail-closed confirmation fence.
    # It does not synchronize a scalar to Python on every management boundary.
    torch._assert_async(
        torch.all((stop > 0) & (stop < target)),
        "Strategy 1 confirmed stop would cross working target",
    )
    return ProtectionRegisters(
        proposed.boundary_ms,
        stop,
        target,
        proposed.accepted,
        proposed.pending_count,
        proposed.pending_min,
        proposed.earned_min,
        proposed.earned_groups,
        torch.where(stop_confirmed, proposed.applied_groups, previous.applied_groups),
    )


def add_admission(x):
    """Exact distinct-break/co-terminated-bar gate for purchases two and three.

    `break_new` compares against the PREVIOUS accepted-ID set; `break_accepted`
    compares against the just-confirmed protection state. Purchases count order
    groups, never the number of partial fills. Financial state refresh between
    accepted adds belongs to the portfolio coordinator.
    """
    at = x["boundary_ms"]
    return (
        (at > 0)
        & (at.remainder(1000) == 0)
        & (x["break_boundary_ms"] == at)
        & x["break_is_resistance"]
        & x["break_new"]
        & x["break_accepted"]
        & (x["quantity"] > 0)
        & ~x["pending_exit"]
        & ~x["pending_entry"]
        & ~x["pending_capital_request"]
        & (x["purchase_ordinal"] >= 2)
        & (x["purchase_ordinal"] <= 3)
        & (x["current_purchase_groups"] == x["purchase_ordinal"] - 1)
        & x["bars_valid"]
        & (x["bar_100_boundary_ms"] == at)
        & (x["bar_1s_boundary_ms"] == at)
        & torch.isfinite(x["macd_100_line"])
        & torch.isfinite(x["macd_100_signal"])
        & torch.isfinite(x["macd_1s_line"])
        & torch.isfinite(x["macd_1s_signal"])
        & (x["macd_100_line"] > x["macd_100_signal"])
        & (x["macd_1s_line"] > x["macd_1s_signal"])
        & (x["open_1s_int"] > 0)
        & (x["close_1s_int"] > x["open_1s_int"])
        & (x["close_100_int"] > 0)
        & (x["close_100_int"] > x["break_midpoint"] * 10000)
        & x["quote_valid"]
        & (x["bid"] > 0)
        & (x["ask"] >= x["bid"])
        & torch.isfinite(x["fresh_bid"])
        & torch.isfinite(x["fresh_ask"])
        & ((x["bid"] - x["fresh_bid"]).abs() <= 1e-9)
        & ((x["ask"] - x["fresh_ask"]).abs() <= 1e-9)
        & (x["stop"] > 0)
        & (x["stop"] < x["bid"])
        & (x["ask"] < x["target"])
    )

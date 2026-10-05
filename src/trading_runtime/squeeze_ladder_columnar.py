"""Prepared ladder masks over full certified completed source observations.

No Strategy 1 candidate mask is consumed. Only survivors may advance frozen
setup state; this module grants no order permission and calculates no indicator.
The caller pins the explicit policy and source attempts before execution.
"""
from dataclasses import dataclass
from math import isfinite

import numpy as np

from .squeeze_ladder_cross import completed_vwap_crossings


REJECT_PRICE = 1
REJECT_QUOTE = 2
REJECT_VWAP = 4
REJECT_LIQUIDITY = 8
REJECT_HISTORY = 16
REJECT_ADMISSION = 32
REJECT_SESSION = 64


@dataclass(frozen=True, slots=True)
class LadderGatePolicy:
    minimum_session_shares: float
    minimum_session_dollars: float
    minimum_trade_rate_10s: float
    minimum_trade_rate_60s: float
    maximum_spread_bps: float
    minimum_price_int: int
    quote_freshness_us: int
    admission_ttl_ms: int
    vwap_buffer_int: int
    acquisition_windows: tuple[tuple[int, int], ...]

    def __post_init__(self):
        for value in (self.minimum_session_shares, self.minimum_session_dollars,
                      self.minimum_trade_rate_10s, self.minimum_trade_rate_60s,
                      self.maximum_spread_bps):
            if type(value) is not float or not isfinite(value) or value <= 0:
                raise ValueError("Ladder liquidity thresholds must be finite positive floats")
        if (type(self.minimum_price_int) is not int or self.minimum_price_int <= 0
                or type(self.quote_freshness_us) is not int or not 0 < self.quote_freshness_us <= 1_000_000
                or type(self.admission_ttl_ms) is not int or not 0 < self.admission_ttl_ms <= 300_000
                or self.admission_ttl_ms % 100
                or type(self.vwap_buffer_int) is not int or not 0 <= self.vwap_buffer_int < 2**53
                or not isinstance(self.acquisition_windows, tuple) or not self.acquisition_windows):
            raise ValueError("Invalid ladder clock/price policy")
        prior = -1
        for window in self.acquisition_windows:
            if (not isinstance(window, tuple) or len(window) != 2
                    or any(type(value) is not int or value % 100 for value in window)):
                raise ValueError("Ladder acquisition windows require completed clocks")
            start, end = window
            if not (prior <= start < end and
                    (0 <= start < end <= 19_800_000 or 43_200_000 <= start < end <= 57_600_000)):
                raise ValueError("Ladder acquisition windows must be ordered extended sessions")
            prior = end


@dataclass(frozen=True, slots=True)
class LadderGateBatch:
    boundary_ms: np.ndarray
    admission_boundary_ms: np.ndarray
    market_rejection: np.ndarray
    market_indices: np.ndarray
    vwap_cross_indices: np.ndarray
    certified_history_through_ms: int | None = None


def compile_ladder_gate(*, policy: LadderGatePolicy, boundary_ms: np.ndarray,
                        evaluation_epoch_us: np.ndarray, close_int: np.ndarray,
                        price_valid: np.ndarray, execution_vwap: np.ndarray,
                        bid_int: np.ndarray, ask_int: np.ndarray,
                        quote_valid: np.ndarray, quote_timestamp_us: np.ndarray,
                        cumulative_volume: np.ndarray, cumulative_notional: np.ndarray,
                        volume_trade_count: np.ndarray,
                        admission_boundaries_ms: np.ndarray,
                        certified_history_through_ms: int | None = None) -> LadderGateBatch:
    """Vectorize crossing and liquidity; missing history is not zero activity.

    Admission clocks come from the certified Signal Stream, not MACD inference.
    Latest admission owns an observation; a cross cannot straddle admissions.
    Rates use the same completed (t-window,t] eligible bucket convention as
    the native gate. Missing buckets invalidate the window until they expire.
    VWAP source bits and rich lineage remain with the caller's certified batch.
    """
    if not isinstance(policy, LadderGatePolicy):
        raise ValueError("Ladder mask requires an explicit frozen policy")
    integer = (boundary_ms, evaluation_epoch_us, close_int, bid_int, ask_int,
               quote_timestamp_us, volume_trade_count)
    floating = (execution_vwap, cumulative_volume, cumulative_notional)
    flags = (price_valid, quote_valid)
    if any(not isinstance(value, np.ndarray) or value.ndim != 1 for value in (*integer, *floating, *flags)):
        raise ValueError("Ladder source requires one-dimensional typed columns")
    n = len(boundary_ms)
    # Only the certified full-prefix loader may supply this availability bound.
    # Sparse absence in a completely read event product is not lost history.
    # Generic/incomplete inputs retain the strict dense-history requirement.
    if certified_history_through_ms is not None and (
            type(certified_history_through_ms) is not int
            or not 0 < certified_history_through_ms <= 57_600_000
            or certified_history_through_ms % 100
            or (n and certified_history_through_ms < int(boundary_ms[-1]))):
        raise ValueError('Ladder certified history does not cover the source prefix')
    if (n > 576_000 or any(len(value) != n for value in (*integer, *floating, *flags))
            or any(value.dtype != np.dtype('int64') for value in integer)
            or any(value.dtype != np.dtype('float64') for value in floating)
            or any(value.dtype.kind not in 'iu' or np.any((value != 0) & (value != 1)) for value in flags)):
        raise ValueError("Ladder source alignment, dtype or bounded size differs")
    admissions = admission_boundaries_ms
    if (not isinstance(admissions, np.ndarray) or admissions.ndim != 1
            or admissions.dtype != np.dtype('int64') or len(admissions) > 576_000
            or np.any(admissions <= 0) or np.any(admissions > 57_600_000)
            or np.any(admissions % 100) or np.any(np.diff(admissions) <= 0)):
        raise ValueError("Ladder admissions require ordered certified completed clocks")
    if (np.any(boundary_ms <= 0) or np.any(boundary_ms > 57_600_000)
            or np.any(boundary_ms % 100) or np.any(np.diff(boundary_ms) <= 0)
            or np.any(evaluation_epoch_us <= 0)
            or (n and np.any(evaluation_epoch_us - boundary_ms * 1000 != evaluation_epoch_us[0] - boundary_ms[0] * 1000))
            or np.any(close_int < 0) or np.any(close_int >= 2**53)
            or np.any(volume_trade_count < 0)
            or (n and np.any(volume_trade_count > np.iinfo(np.int64).max // n))):
        raise ValueError("Ladder source clock, price or count domain differs")
    for values in (cumulative_volume, cumulative_notional):
        if np.any(~np.isfinite(values)) or np.any(values < 0) or np.any(np.diff(values) < -1e-7):
            raise ValueError("Ladder cumulative liquidity is invalid")
    admitted = np.zeros(n, dtype=np.int64)
    indices = np.searchsorted(admissions, boundary_ms, side='right') - 1
    if len(admissions):
        known = indices >= 0
        admitted[known] = admissions[indices[known]]
    admission_ok = (admitted > 0) & (boundary_ms - admitted < policy.admission_ttl_ms)
    session_ok = np.zeros(n, dtype=bool)
    for start, end in policy.acquisition_windows:
        session_ok |= (boundary_ms > start) & (boundary_ms < end)
    counts = np.r_[np.int64(0), np.cumsum(volume_trade_count, dtype=np.int64)]
    left10 = np.searchsorted(boundary_ms, boundary_ms - 10_000, side='right')
    left60 = np.searchsorted(boundary_ms, boundary_ms - 60_000, side='right')
    rate10 = (counts[1:] - counts[left10]) / 10.
    rate60 = (counts[1:] - counts[left60]) / 60.
    history_ok = np.zeros(n, dtype=bool)
    if n:
        gaps = np.r_[boundary_ms[0] > 100, np.diff(boundary_ms) != 100]
        missing_through = np.maximum.accumulate(np.where(gaps, boundary_ms - 100, 0))
        history_ok = missing_through <= np.maximum(boundary_ms - 60_000, 0)
        if certified_history_through_ms is not None:
            history_ok[:] = True
    quote_age = evaluation_epoch_us - np.maximum(quote_timestamp_us, 0)
    quote_ok = ((quote_valid == 1) & (quote_timestamp_us > 0)
                & (bid_int > 0) & (ask_int >= bid_int) & (ask_int < 2**53)
                & (quote_age >= 0) & (quote_age <= policy.quote_freshness_us))
    spread = np.full(n, np.inf)
    spread[quote_ok] = ((ask_int[quote_ok].astype(np.float64) - bid_int[quote_ok]) * 20_000
                        / (ask_int[quote_ok].astype(np.float64) + bid_int[quote_ok]))
    with np.errstate(over='ignore', invalid='ignore'):
        vwap_ok = np.isfinite(execution_vwap) & (execution_vwap > 0) & (execution_vwap * 10_000 < close_int)
    liquidity_ok = ((cumulative_volume >= policy.minimum_session_shares)
                    & (cumulative_notional >= policy.minimum_session_dollars)
                    & (rate10 >= policy.minimum_trade_rate_10s)
                    & (rate60 >= policy.minimum_trade_rate_60s)
                    & (spread <= policy.maximum_spread_bps))
    reasons = np.zeros(n, dtype=np.uint8)
    for bit, good in ((REJECT_PRICE, (price_valid == 1) & (close_int >= policy.minimum_price_int)),
                      (REJECT_QUOTE, quote_ok), (REJECT_VWAP, vwap_ok),
                      (REJECT_LIQUIDITY, liquidity_ok), (REJECT_HISTORY, history_ok),
                      (REJECT_ADMISSION, admission_ok), (REJECT_SESSION, session_ok)):
        reasons[~good] |= bit
    crossing = completed_vwap_crossings(boundary_ms=boundary_ms, close_int=close_int,
        price_valid=price_valid, execution_vwap=execution_vwap,
        reference_available_ms=boundary_ms, reference_valid=np.ones(n, dtype=np.uint8),
        admitted_at_ms=0, buffer_int=policy.vwap_buffer_int, maximum_reference_age_ms=0).crossing
    same_admission = np.zeros(n, dtype=bool)
    same_admission[1:] = (admitted[:-1] == admitted[1:]) & (boundary_ms[:-1] >= admitted[1:])
    arrays = (boundary_ms.copy(), admitted, reasons, np.flatnonzero(reasons == 0),
              np.flatnonzero(crossing & same_admission & (reasons == 0)))
    for value in arrays:
        value.setflags(write=False)
    return LadderGateBatch(*arrays, certified_history_through_ms)

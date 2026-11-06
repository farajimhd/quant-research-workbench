"""Append twelve deterministic time features to the 21-channel shard values.

This module contains no learned layers, loading, training, or market scaling.
It uses only completed-bar clocks and an explicitly supplied exchange schedule.
Read ``BarInputTransformV1.__call__`` for the numerical feature definitions.
Calendar preparation happens once in the constructor, outside the batch path.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.bar_gpt.v5.shards.contract import CHANNELS, FRAMES, WIDTH


TIME_CHANNELS = (
    "premarket", "regular_hours", "after_hours",
    "bar_index", "log_timeframe_seconds",
    "intraday_sin", "intraday_cos", "log_gap_ratio",
    "weekday_sin", "weekday_cos", "iso_week_sin", "iso_week_cos",
)
FEATURE_NAMES = CHANNELS + TIME_CHANNELS  # Stable order for all [B,64,33] arrays.
TIMEZONE = "America/New_York"
DAILY_US = 86_400_000_000  # Daily identity; its stored interval is 04:00–20:00 NY.


@dataclass(frozen=True)
class TransformedBars:
    """Model inputs: Float32 ``values[B,64,33]`` and bool ``valid[B,64,33]``.

    Feature order is ``FEATURE_NAMES``. Invalid values are zero placeholders,
    distinguishable from observed zeros through ``valid``. No ticker, absolute
    timestamp, or year number is included. Calendar/timeframe context is explicit.
    Artificial reconstruction masking is separate from these evidence masks.
    """

    values: np.ndarray
    valid: np.ndarray


class BarInputTransformV1:
    """Prepare the agreed time-aware inputs without changing market semantics.

    Supply an XNYS-compatible session schedule covering every sample date, e.g.
    ``exchange_calendars.get_calendar('XNYS', start=..., end=...).schedule``.
    The caller owns calendar selection/versioning; no calendar is downloaded or
    inferred here. Session flags describe interval overlap, not just end time.
    Consequently a full extended-session daily bar has all three flags equal to 1.
    """

    def __init__(self, session_schedule: pd.DataFrame):
        """Cache session boundaries from an explicit exchange-calendar schedule.

        session_schedule: DataFrame indexed by unique, increasing session-date
            labels at midnight, with timezone-aware ``open`` and ``close`` columns.
            UTC-midnight index labels are treated as dates, not converted to NY.
            Early closes must already be present in the supplied schedule.
        """
        if not {"open", "close"} <= set(session_schedule.columns):
            raise ValueError("Schedule must contain timezone-aware open and close columns.")
        dates = pd.DatetimeIndex(session_schedule.index).tz_localize(None)
        if (dates.empty or dates.hasnans or not dates.is_unique
                or not dates.is_monotonic_increasing or not dates.equals(dates.normalize())):
            raise ValueError("Schedule index must be nonempty, ordered, unique session dates.")
        for column in ("open", "close"):
            if (not isinstance(session_schedule[column].dtype, pd.DatetimeTZDtype)
                    or session_schedule[column].isna().any()):
                raise ValueError(f"Schedule {column} must be timezone-aware and complete.")

        # Cache date keys in nanoseconds and boundaries in UTC microseconds.
        self._dates_ns = dates.as_unit("ns").asi8
        self._regular_open = pd.DatetimeIndex(session_schedule["open"]).as_unit("us").asi8
        self._regular_close = pd.DatetimeIndex(session_schedule["close"]).as_unit("us").asi8
        # Localize wall-clock boundaries rather than adding hours to UTC midnight.
        self._extended_open = (dates + pd.Timedelta(hours=4)).tz_localize(TIMEZONE).as_unit("us").asi8
        self._extended_close = (dates + pd.Timedelta(hours=20)).tz_localize(TIMEZONE).as_unit("us").asi8
        if not np.all((self._extended_open < self._regular_open)
                      & (self._regular_open < self._regular_close)
                      & (self._regular_close < self._extended_close)):
            raise ValueError("Regular sessions must lie strictly inside 04:00–20:00 NY.")

    def __call__(
        self,
        values: np.ndarray,
        valid: np.ndarray,
        end_us: np.ndarray,
        timeframe_us: np.ndarray | int,
    ) -> TransformedBars:
        """Transform one batch using vectorized NumPy/Pandas operations.

        Args:
            values: floating [B,64,21], from shard ``values``; prices, activity,
                and halt channels remain unscaled. Invalid fields may hold NaN.
            valid: bool or 0/1 integer [B,64,21], from shard evidence masks.
            end_us: integer [B,64], UTC completed-bar end timestamps in microseconds,
                strictly increasing within each sample. Session gaps are allowed.
            timeframe_us: integer scalar or [B], from shard timeframe metadata;
                supported durations are 100ms, 1s, 10s, 1m, 1h, and 1d.

        Returns:
            TransformedBars containing Float32 values and bool validity [B,64,33].
            The first gap is zero with validity False: no predecessor is supplied.
            The remaining eleven time channels are valid for every accepted bar.

        Formulas:
            index = i/63; timeframe = log1p(duration_seconds).
            clock = sin/cos(2*pi*NY_seconds_since_midnight/86400).
            gap[t] = log1p((end[t]-end[t-1])/timeframe_us), for t>0.
            weekday = sin/cos(2*pi*Monday_zero_weekday/7).
            week = sin/cos(2*pi*(ISO_week-1)/weeks_in_that_ISO_year).
            Session flags are positive-duration overlaps of [bar_start,bar_end)
            with [04:00,open), [open,close), and [close,20:00).

        These transformations never use market-value statistics, so calculating
        them before artificial masking cannot leak hidden prices or activity.
        """
        values, valid, end_us = np.asarray(values), np.asarray(valid), np.asarray(end_us)
        if values.ndim != 3 or values.shape[1:] != (WIDTH, len(CHANNELS)) or not len(values):
            raise ValueError("values must have nonempty shape [B,64,21].")
        batch = len(values)
        if not np.issubdtype(values.dtype, np.floating):
            raise ValueError("Market values must be floating point.")
        if (valid.shape != values.shape or valid.dtype.kind not in "bui"
                or not np.isin(valid, (0, 1)).all()):
            raise ValueError("valid must contain boolean/0-1 evidence masks [B,64,21].")
        observed = valid.astype(bool, copy=False)  # [B,64,21]; source masks, not augmentation masks.
        if not np.isfinite(values[observed]).all():
            raise ValueError("Observed market values must be finite.")
        if (end_us.shape != (batch, WIDTH) or end_us.dtype.kind not in "ui"
                or np.any(end_us <= 0) or np.any(end_us > np.iinfo(np.int64).max)):
            raise ValueError("end_us must contain positive signed-64-bit-compatible integers [B,64].")
        ends = end_us.astype(np.int64, copy=False)  # [B,64], before subtraction to avoid unsigned wrap.
        if np.any(np.diff(ends, axis=1) <= 0):
            raise ValueError("Bar end timestamps must increase strictly within each sample.")
        frames = np.asarray(timeframe_us)
        if frames.dtype.kind not in "ui" or frames.shape not in ((), (batch,)):
            raise ValueError("timeframe_us must be an integer scalar or [B].")
        if not np.isin(frames, FRAMES).all():
            raise ValueError("Unsupported timeframe for the current shard contract.")
        frames = np.broadcast_to(frames, (batch,)).astype(np.int64, copy=False)  # Scalar/[B] → [B].

        local = pd.to_datetime(ends.reshape(-1), unit="us", utc=True).tz_convert(TIMEZONE)
        # [B*64] local dates look up historical regular opens/closes, including early closes.
        dates_ns = local.tz_localize(None).normalize().as_unit("ns").asi8
        indices = np.searchsorted(self._dates_ns, dates_ns)
        safe_indices = np.minimum(indices, len(self._dates_ns)-1)
        if np.any(indices == len(self._dates_ns)) or np.any(self._dates_ns[safe_indices] != dates_ns):
            raise ValueError("Bar date is absent from the supplied trading-session calendar.")
        extended_open = self._extended_open[indices].reshape(batch, WIDTH)
        extended_close = self._extended_close[indices].reshape(batch, WIDTH)
        regular_open = self._regular_open[indices].reshape(batch, WIDTH)
        regular_close = self._regular_close[indices].reshape(batch, WIDTH)

        # Daily bars span the actual extended session, not a rolling 24-hour interval.
        starts = np.where(frames[:, None] == DAILY_US, extended_open, ends - frames[:, None])
        if np.any(starts < extended_open) or np.any(ends > extended_close):
            raise ValueError("Bar interval is outside the shards' 04:00–20:00 session.")
        daily = frames[:, None] == DAILY_US  # [B,1], broadcast over bar positions.
        if np.any(daily & (ends != extended_close)):
            raise ValueError("Daily bars must end at 20:00 New York.")
        if np.any(~daily & ((ends - extended_open) % frames[:, None] != 0)):
            raise ValueError("Intraday bars must align to the scheduled timeframe grid.")

        time_values = np.zeros((batch, WIDTH, len(TIME_CHANNELS)), dtype=np.float32)
        # Strict inequalities exclude mere boundary contact from session overlap.
        time_values[..., 0] = (starts < regular_open) & (ends > extended_open)
        time_values[..., 1] = (starts < regular_close) & (ends > regular_open)
        time_values[..., 2] = (starts < extended_close) & (ends > regular_close)
        time_values[..., 3] = np.arange(WIDTH, dtype=np.float32) / (WIDTH-1)  # [64] → [B,64].
        time_values[..., 4] = np.log1p(frames / 1_000_000.0)[:, None]  # [B,1] → [B,64].

        seconds = (local.hour.to_numpy()*3600 + local.minute.to_numpy()*60
                   + local.second.to_numpy() + local.microsecond.to_numpy()/1_000_000.0)
        clock_angle = 2*np.pi*seconds/86400.0  # [B*64], using bar-end wall-clock time.
        time_values[..., 5] = np.sin(clock_angle).reshape(batch, WIDTH)
        time_values[..., 6] = np.cos(clock_angle).reshape(batch, WIDTH)
        time_values[:, 1:, 7] = np.log1p(np.diff(ends, axis=1) / frames[:, None])  # [B,63].

        weekday_angle = 2*np.pi*local.dayofweek.to_numpy()/7.0  # Monday=0, Sunday=6.
        time_values[..., 8] = np.sin(weekday_angle).reshape(batch, WIDTH)
        time_values[..., 9] = np.cos(weekday_angle).reshape(batch, WIDTH)
        iso = local.isocalendar()  # ISO week-year can differ from the calendar year.
        iso_years = iso["year"].to_numpy(dtype=np.int64)
        unique_years, inverse = np.unique(iso_years, return_inverse=True)
        # December 28 always belongs to the final ISO week (52 or 53) of its year.
        december_28 = pd.to_datetime(np.char.add(unique_years.astype(str), "-12-28"))
        weeks_per_year = december_28.isocalendar()["week"].to_numpy(dtype=np.float64)[inverse]
        week_angle = 2*np.pi*(iso["week"].to_numpy(dtype=np.float64)-1)/weeks_per_year
        time_values[..., 10] = np.sin(week_angle).reshape(batch, WIDTH)
        time_values[..., 11] = np.cos(week_angle).reshape(batch, WIDTH)

        time_valid = np.ones_like(time_values, dtype=bool)  # [B,64,12].
        time_valid[:, 0, 7] = False  # The preceding timestamp is absent from the shard sample.
        clean_values = np.where(observed, values, 0).astype(np.float32)  # Preserve observed market values.
        if not np.isfinite(clean_values).all():
            raise ValueError("Market values overflow Float32.")
        return TransformedBars(
            values=np.concatenate((clean_values, time_values), axis=-1),  # 21+12 → [B,64,33].
            valid=np.concatenate((observed, time_valid), axis=-1),  # Matching [B,64,33] evidence.
        )

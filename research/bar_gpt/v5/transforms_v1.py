"""Append twelve deterministic time features to the 21-channel shard values.

This module contains no learned layers, loading, training, or market scaling.
It uses only completed-bar clocks and an explicitly supplied exchange schedule.
Read ``BarInputTransformV1.forward`` for the numerical feature definitions.
Calendar preparation happens once in the constructor, outside the batch path.
"""

from dataclasses import dataclass

import pandas as pd
import torch
from torch import nn

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

    values: torch.Tensor
    valid: torch.Tensor


class BarInputTransformV1(nn.Module):
    """Prepare the agreed time-aware inputs without changing market semantics.

    Supply an XNYS-compatible session schedule covering every sample date, e.g.
    ``exchange_calendars.get_calendar('XNYS', start=..., end=...).schedule``.
    The caller owns calendar selection/versioning; no calendar is downloaded or
    inferred here. Session flags describe interval overlap, not just end time.
    Consequently a full extended-session daily bar has all three flags equal to 1.

    Construct once with ``transform = BarInputTransformV1(schedule).to(device)``.
    Call with Torch tensors already on that device; outputs stay on that device.
    Calendar buffers are saved in ``state_dict`` alongside the parent model.
    """

    def __init__(self, session_schedule: pd.DataFrame):
        """Cache session boundaries from an explicit exchange-calendar schedule.

        session_schedule: DataFrame indexed by unique, increasing session-date
            labels at midnight, with timezone-aware ``open`` and ``close`` columns.
            UTC-midnight index labels are treated as dates, not converted to NY.
            Early closes must already be present in the supplied schedule.
        """
        super().__init__()  # Calendar buffers follow module.to(device) and enter state_dict.
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

        # Pandas is confined to initialization: resolve historical DST and calendar rules once.
        extended_open = (dates + pd.Timedelta(hours=4)).tz_localize(TIMEZONE).as_unit("us").asi8
        extended_close = (dates + pd.Timedelta(hours=20)).tz_localize(TIMEZONE).as_unit("us").asi8
        regular_open = pd.DatetimeIndex(session_schedule["open"]).as_unit("us").asi8
        regular_close = pd.DatetimeIndex(session_schedule["close"]).as_unit("us").asi8
        if not ((extended_open < regular_open) & (regular_open < regular_close)
                & (regular_close < extended_close)).all():
            raise ValueError("Regular sessions must lie strictly inside 04:00–20:00 NY.")
        iso = dates.isocalendar()  # ISO year can differ from the date's calendar year.
        december_28 = pd.to_datetime(iso["year"].astype(str) + "-12-28")
        weeks_per_year = pd.DatetimeIndex(december_28).isocalendar()["week"]
        # Integer buffers retain exact clocks even if the parent model uses half precision.
        columns = (
            extended_open, extended_close, regular_open, regular_close,
            dates.dayofweek.to_numpy(), iso["week"].to_numpy(dtype="int64") - 1,
            weeks_per_year.to_numpy(dtype="int64"),
        )
        self.register_buffer("calendar", torch.stack([
            torch.tensor(column, dtype=torch.int64) for column in columns
        ], dim=1))  # Seven [S] columns → [S,7], one row per trading session.
        self.register_buffer("session_opens", self.calendar[:, 0].contiguous())  # [S], search keys.
        self.register_buffer("supported_frames", torch.tensor(FRAMES, dtype=torch.int64))

    def forward(
        self,
        values: torch.Tensor,
        valid: torch.Tensor,
        end_us: torch.Tensor,
        timeframe_us: torch.Tensor | int,
    ) -> TransformedBars:
        """Transform one batch entirely with vectorized Torch on the input device.

        Args:
            values: Torch floating [B,64,21], from shard ``values``; prices, activity,
                and halt channels remain unscaled. Invalid fields may hold NaN.
            valid: Torch bool or 0/1 integer [B,64,21], from shard evidence masks.
            end_us: Torch int64 [B,64], UTC completed-bar end timestamps in microseconds,
                strictly increasing within each sample. Session gaps are allowed.
            timeframe_us: Python int or Torch int64 scalar/[B], from shard timeframe metadata;
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

        Move this module and all input tensors to the same device before calling.
        No Pandas/NumPy operations or bulk CPU transfers occur in forward. Eager
        validation raises ValueError on bad evidence; these scalar checks synchronize
        CUDA, so this checked path does not promise asynchronous execution.

        These transformations never use market-value statistics, so calculating
        them before artificial masking cannot leak hidden prices or activity.
        """
        if not all(isinstance(item, torch.Tensor) for item in (values, valid, end_us)):
            raise TypeError("values, valid, and end_us must be Torch tensors.")
        if any(item.device != self.calendar.device for item in (values, valid, end_us)):
            raise ValueError("Move the transform and all input tensors to the same device.")
        if values.ndim != 3 or values.shape[1:] != (WIDTH, len(CHANNELS)) or not len(values):
            raise ValueError("values must have nonempty shape [B,64,21].")
        batch, device = len(values), values.device
        if not values.is_floating_point():
            raise ValueError("Market values must be floating point.")
        mask_types = (torch.bool, torch.uint8, torch.int8, torch.int16, torch.int32, torch.int64)
        if (valid.shape != values.shape or valid.dtype not in mask_types
                or not ((valid == 0) | (valid == 1)).all()):
            raise ValueError("valid must contain boolean/0-1 evidence masks [B,64,21].")
        observed = valid.bool()  # [B,64,21]; evidence masks, not augmentation masks.
        if not (torch.isfinite(values) | ~observed).all():
            raise ValueError("Observed market values must be finite.")
        if end_us.shape != (batch, WIDTH) or end_us.dtype != torch.int64 or (end_us <= 0).any():
            raise ValueError("end_us must contain positive int64 timestamps [B,64].")
        gaps = torch.diff(end_us, dim=1)  # [B,64] → [B,63], exact integer subtraction.
        if (gaps <= 0).any():
            raise ValueError("Bar end timestamps must increase strictly within each sample.")
        if type(timeframe_us) is int:
            timeframe_us = torch.tensor(timeframe_us, dtype=torch.int64, device=device)
        if (not isinstance(timeframe_us, torch.Tensor) or timeframe_us.dtype != torch.int64
                or timeframe_us.device != device or timeframe_us.shape not in ((), (batch,))):
            raise ValueError("timeframe_us must be an int64 scalar or [B] on the input device.")
        if not torch.isin(timeframe_us, self.supported_frames).all():
            raise ValueError("Unsupported timeframe for the current shard contract.")
        frames = timeframe_us.expand(batch)  # Scalar/[B] → [B], without copying.

        # Select the latest session open, then reject bars outside that exact session.
        # Holiday/weekend/out-of-coverage timestamps cannot inherit the previous session.
        indices = torch.searchsorted(self.session_opens, end_us.contiguous(), right=True) - 1
        rows = self.calendar[indices.clamp_min(0)]  # [B,64] → [B,64,7], bounded lookup.
        extended_open, extended_close, regular_open, regular_close = rows[..., :4].unbind(-1)
        daily = frames[:, None] == DAILY_US  # [B,1], broadcast over bar positions.
        starts = torch.where(daily, extended_open, end_us - frames[:, None])
        if ((indices < 0) | (starts < extended_open) | (end_us > extended_close)).any():
            raise ValueError("Bar interval is outside the supplied calendar's 04:00–20:00 session.")
        if (daily & (end_us != extended_close)).any():
            raise ValueError("Daily bars must end at 20:00 New York.")
        if (~daily & ((end_us - extended_open) % frames[:, None] != 0)).any():
            raise ValueError("Intraday bars must align to the scheduled timeframe grid.")

        time_values = torch.zeros((batch, WIDTH, len(TIME_CHANNELS)), dtype=torch.float32, device=device)
        # Strict inequalities exclude mere boundary contact from session overlap.
        time_values[..., 0] = (starts < regular_open) & (end_us > extended_open)
        time_values[..., 1] = (starts < regular_close) & (end_us > regular_open)
        time_values[..., 2] = (starts < extended_close) & (end_us > regular_close)
        # Relative sequence position broadcasts [64] across all samples → [B,64].
        time_values[..., 3] = torch.arange(WIDTH, device=device, dtype=torch.float32) / (WIDTH - 1)
        time_values[..., 4] = torch.log1p(frames.double() / 1_000_000)[:, None]  # [B,1] → [B,64].

        # Subtract exact int64 epochs BEFORE converting to float: retain 100ms precision.
        # NY has no UTC-offset transition inside a trading session's 04:00–20:00 interval.
        seconds = (end_us - extended_open).double() / 1_000_000 + 4 * 3600
        clock_angle = 2 * torch.pi * seconds / 86400  # [B,64], local bar-end clock phase.
        time_values[..., 5] = torch.sin(clock_angle)
        time_values[..., 6] = torch.cos(clock_angle)
        time_values[:, 1:, 7] = torch.log1p(gaps.double() / frames[:, None])  # [B,63].
        weekday_angle = 2 * torch.pi * rows[..., 4].double() / 7  # Monday=0.
        time_values[..., 8] = torch.sin(weekday_angle)
        time_values[..., 9] = torch.cos(weekday_angle)
        week_angle = 2 * torch.pi * rows[..., 5].double() / rows[..., 6]  # ISO 52/53-week years.
        time_values[..., 10] = torch.sin(week_angle)
        time_values[..., 11] = torch.cos(week_angle)

        time_valid = torch.ones_like(time_values, dtype=torch.bool)  # [B,64,12].
        time_valid[:, 0, 7] = False  # No predecessor exists in this shard sample.
        clean_values = torch.where(observed, values, 0).float()  # [B,64,21], unscaled, differentiable.
        if not torch.isfinite(clean_values).all():
            raise ValueError("Market values overflow Float32.")
        return TransformedBars(
            values=torch.cat((clean_values, time_values), dim=-1),  # 21+12 → [B,64,33].
            valid=torch.cat((observed, time_valid), dim=-1),  # Matching [B,64,33] evidence.
        )

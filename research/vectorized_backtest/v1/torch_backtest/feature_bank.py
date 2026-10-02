"""Compile requested atomic feature histories into causal resident GPU lanes.

One atom is ONE field with an explicit unit, source resolution, completed-bar
lag and bounded window. OHLC, MACD line/signal, float and shares are separate
atoms. Missing evidence is NaN. An optimizer can change lag/window (1..64),
field, operation or threshold; a changed input contract is prepared once before
replay. This avoids rescanning or dispatching Python callbacks during ticks.

Bar/indicator authority is the certified market tape. Float/share authority is
the existing point-in-time QMD resolver. RVOL requires a hash-validated QMD
prior-20-session baseline: no current float or ad-hoc volume proxy is used.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import torch

from src.backend.backtest_market_data import market_day_boundary
from src.backend.session_relative_volume import validate_baseline
from src.backend.strategy_one_entry_context import asof_reference

BAR_UNITS = {
    **{
        name: {"price", "integer_price"}
        for name in (
            "open_int",
            "high_int",
            "low_int",
            "close_int",
            "bid_int",
            "ask_int",
        )
    },
    **{
        name: {"price"}
        for name in (
            "spread",
            "execution_vwap",
            "previous_close",
            "macd_line",
            "macd_signal",
            "macd_histogram",
            "atr_14",
            *(f"ema_{n}" for n in (7, 9, 12, 15, 20, 26, 50)),
        )
    },
    **{
        name: {"shares"}
        for name in (
            "volume",
            "execution_volume",
            "cumulative_volume",
            "cumulative_execution_volume",
            "bid_size",
            "ask_size",
        )
    },
    **{
        name: {"money"}
        for name in (
            "notional",
            "execution_notional",
            "cumulative_notional",
            "cumulative_execution_notional",
        )
    },
    **{
        name: {"count"}
        for name in (
            "trade_count",
            "source_trade_count",
            "event_count",
            "quote_event_count",
            "bucket_index",
        )
    },
    **{
        name: {"boolean"}
        for name in (
            "price_valid",
            "extremes_valid",
            "quote_valid",
            "rsi_ready",
            "atr_ready",
        )
    },
    **{
        name: {"microseconds"}
        for name in ("first_event_us", "last_event_us", "quote_timestamp_us")
    },
    "boundary_ms": {"milliseconds"},
    "resolution_ms": {"milliseconds"},
    "indicator_resolution_ms": {"milliseconds"},
    "rsi_14": {"rsi_points"},
}


@dataclass(frozen=True)
class FeatureAtom:
    name: str
    field: str
    unit: str
    resolution_ms: int = 1000
    lag: int = 0
    window: int = 1
    reduction: str = "last"
    source: str = "bars"  # bars / reference / rvol

    def validate(self):
        if self.unit not in {
            "price",
            "integer_price",
            "shares",
            "money",
            "count",
            "ratio",
            "bps",
            "return",
            "rsi_points",
            "milliseconds",
            "microseconds",
            "boolean",
        }:
            raise ValueError("Feature unit needs a registered physical representation")
        if (
            not self.name
            or not self.unit
            or not 0 <= self.lag <= 63
            or not 1 <= self.window <= 64
        ):
            raise ValueError("Feature needs an atomic name/unit and a bounded history")
        if self.lag + self.window > 64:
            raise ValueError("Combined history exceeds the declared 64-bar envelope")
        if self.reduction not in ("last", "min", "max", "mean", "sum"):
            raise ValueError("Unknown primitive history reduction")
        if self.reduction == "last" and self.window != 1:
            raise ValueError("Last selects one atomic observation")
        if self.unit == "boolean" and self.reduction not in ("last", "min", "max"):
            raise ValueError(
                "Boolean history supports last/all/any, not numeric averages"
            )
        if self.source not in ("bars", "reference", "rvol"):
            raise ValueError("Unknown feature authority")
        if self.source == "bars" and self.unit not in BAR_UNITS.get(self.field, set()):
            raise ValueError(
                "Atomic bar field/unit is outside the certified vocabulary"
            )
        if (self.source == "reference" and self.unit != "shares") or (
            self.source == "rvol" and self.unit != "ratio"
        ):
            raise ValueError("Reference/RVOL unit differs from its producer contract")
        if self.source != "bars" and (self.lag or self.window != 1):
            raise ValueError("Reference/RVOL inputs use their own as-of contract")
        if self.source == "reference" and self.field not in (
            "float_shares",
            "shares_outstanding",
        ):
            raise ValueError("Unregistered reference feature")
        if self.source == "rvol" and self.field != "session_rvol":
            raise ValueError("Unregistered RVOL feature")
        return self


@dataclass
class FeatureBank:
    atoms: tuple[FeatureAtom, ...]
    values: torch.Tensor  # [T_100ms,N,F]; only selected dependencies are materialized.
    evidence: dict

    def at(self, index, batch):
        row = (
            self.values.index_select(0, index)
            .squeeze(0)
            .unsqueeze(0)
            .expand(batch, -1, -1)
        )
        return {atom.name: row[..., field] for field, atom in enumerate(self.atoms)}

    def to(self, device):
        return FeatureBank(self.atoms, self.values.to(device), self.evidence)


def relative_volume_rows(rows, profile, through_ms):
    """Advance the denominator every completed second, including quiet ones.

    A certified sparse bar tape proves zero new volume in absent event buckets.
    The cumulative numerator carries forward, but the prior-session denominator
    can still increase. Holding the previous ratio would overstate RVOL.
    Baseline certification is mandatory in the caller before this projection.
    """
    if rows.is_empty() or rows["volume"].null_count() or rows["volume"].min() < 0:
        raise ValueError("RVOL requires valid completed canonical volume evidence")
    seconds = through_ms // 1000
    if len(profile) <= seconds:
        raise ValueError("RVOL baseline does not cover the declared session")
    cumulative = rows.sort("boundary_ms").select(
        "boundary_ms", pl.col("volume").cum_sum().alias("numerator")
    )
    return (
        pl.DataFrame(
            {
                "boundary_ms": range(1000, (seconds + 1) * 1000, 1000),
                "denominator": profile[1 : seconds + 1],
            },
            schema_overrides={"denominator": pl.Float64},
        )
        .join(cumulative, on="boundary_ms", how="left")
        .sort("boundary_ms")
        .with_columns(pl.col("numerator").forward_fill().fill_null(0))
        .select(
            "boundary_ms",
            pl.when(pl.col("denominator") > 0)
            .then(pl.col("numerator") / pl.col("denominator"))
            .otherwise(None)
            .alias("value"),
        )
    )


def prepare_features(
    directory,
    atoms,
    *,
    reference_client=None,
    conids=None,
    baselines=None,
    reference_updates=None,
    maximum_bytes=2 * 1024**3,
):
    """Prepare only the policy's declared inputs; never use journal entries.

    Reference updates are optional known-at boundaries from a certified source
    feed. Without them, the start-of-session snapshot is held unchanged, which
    is causal and explicitly recorded. Missing point-in-time identity raises;
    unavailable float/share values remain unknown. Required RVOL baselines
    must validate before any replay starts. Later availability cannot leak back.
    """
    atoms = tuple(atom.validate() for atom in atoms)
    if len({atom.name for atom in atoms}) != len(atoms):
        raise ValueError("Feature atoms need unique names")
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    required_bytes = (
        manifest["through_ms"] // 100 * len(manifest["files"]) * len(atoms) * 8
    )
    if required_bytes > maximum_bytes:
        raise MemoryError(
            "Declared atomic feature bank exceeds the resident memory budget"
        )
    # Call the main tape verifier first: it owns all path/hash/key checks.
    from .strategy_one_tape import prepare

    verified = prepare(directory)
    tickers, through = verified.tickers, verified.through_ms
    del verified
    frames = [pl.read_parquet(row["path"]) for row in manifest["files"]]
    source = pl.concat(frames, how="diagonal_relaxed")
    session = source["session_date"].unique().item()
    if not isinstance(session, date):
        session = date.fromisoformat(str(session))
    clocks = pl.DataFrame(
        {"boundary_ms": pl.int_range(100, through + 100, step=100, eager=True)}
    )
    origin = market_day_boundary(session, 0)
    columns, evidence = [], {"session_date": str(session), "sources": {}}
    for atom in atoms:
        listing_columns = []
        for ticker in tickers:
            ticker_source = source.filter(pl.col("ticker") == ticker)
            if atom.source == "bars":
                if atom.field not in ticker_source.columns:
                    raise ValueError(
                        "Required atomic source column is absent: " + atom.field
                    )
                values = ticker_source[atom.field]
                if (
                    values.dtype.is_integer()
                    and values.max() is not None
                    and values.max() > 2**53
                ):
                    raise ValueError(
                        "Atomic integer source cannot be represented losslessly in the feature bank"
                    )
                rows = ticker_source.filter(
                    pl.col("resolution_ms") == atom.resolution_ms
                ).sort("boundary_ms")
                if not rows.height:
                    raise ValueError(
                        f"Required source resolution is absent: {ticker}/{atom.resolution_ms}"
                    )
                value = pl.col(atom.field).cast(pl.Float64)
                if atom.field.endswith("_int") and atom.field in (
                    "open_int",
                    "close_int",
                    "high_int",
                    "low_int",
                ):
                    value = (
                        pl.when(pl.col("price_valid") > 0).then(value).otherwise(None)
                    )
                if atom.field in ("high_int", "low_int"):
                    value = (
                        pl.when(pl.col("extremes_valid") > 0)
                        .then(value)
                        .otherwise(None)
                    )
                if atom.field in (
                    "bid_int",
                    "ask_int",
                    "bid_size",
                    "ask_size",
                    "spread",
                ):
                    value = (
                        pl.when(pl.col("quote_valid") > 0).then(value).otherwise(None)
                    )
                if atom.field in ("rsi_14", "atr_14"):
                    ready = "rsi_ready" if atom.field == "rsi_14" else "atr_ready"
                    value = pl.when(pl.col(ready) > 0).then(value).otherwise(None)
                if atom.field.endswith("_int") and atom.unit == "price":
                    value = value / 10_000
                if atom.window > 1:
                    count = value.is_not_null().cast(pl.Int64).rolling_sum(atom.window)
                    rolled = getattr(value, "rolling_" + atom.reduction)(atom.window)
                    value = pl.when(count == atom.window).then(rolled).otherwise(None)
                rows = rows.select("boundary_ms", value.shift(atom.lag).alias("value"))
                aligned = clocks.join_asof(rows, on="boundary_ms", strategy="backward")[
                    "value"
                ]
                evidence["sources"][atom.name] = {
                    "authority": "certified_market_tape",
                    "resolution_ms": atom.resolution_ms,
                    "lag": atom.lag,
                    "window": atom.window,
                    "reduction": atom.reduction,
                }
            elif atom.source == "reference":
                if reference_client is None or not conids or ticker not in conids:
                    raise ValueError(
                        "Required dated reference reader/conid identity is unavailable"
                    )
                boundaries = [
                    0,
                    *sorted(set((reference_updates or {}).get(ticker, ()))),
                ]
                if any(
                    type(clock) is not int or not 0 <= clock <= through
                    for clock in boundaries
                ):
                    raise ValueError("Reference update clocks exceed the session")
                snapshots, records = [], []
                for clock in boundaries:
                    row = asof_reference(
                        reference_client,
                        session=session,
                        ticker=ticker,
                        conid=conids[ticker],
                        entry_at=origin + timedelta(milliseconds=clock),
                    )
                    snapshots.append(
                        row[
                            "float_value"
                            if atom.field == "float_shares"
                            else "shares_value"
                        ]
                    )
                    records.append({"known_at_ms": clock, **row})
                aligned = clocks.join_asof(
                    pl.DataFrame(
                        {"boundary_ms": boundaries, "value": snapshots},
                        schema_overrides={"value": pl.Float64},
                    ),
                    on="boundary_ms",
                    strategy="backward",
                )["value"]
                evidence["sources"].setdefault(
                    atom.name,
                    {"authority": "q_live_point_in_time_reference", "tickers": {}},
                )["tickers"][ticker] = records
            else:
                baseline = (baselines or {}).get(ticker)
                if baseline is None:
                    raise ValueError(
                        "Required certified prior-20-session RVOL baseline is absent: "
                        + ticker
                    )
                validate_baseline(baseline, ticker, session.isoformat())
                rows = ticker_source.filter(pl.col("resolution_ms") == 1000).sort(
                    "boundary_ms"
                )
                if "volume" not in rows.columns:
                    raise ValueError(
                        "RVOL requires canonical bar volume, not eligible execution volume"
                    )
                rows = relative_volume_rows(rows, baseline["profiles"][ticker], through)
                aligned = clocks.join_asof(rows, on="boundary_ms", strategy="backward")[
                    "value"
                ]
                evidence["sources"].setdefault(
                    atom.name,
                    {"authority": "qmd_prior_20_session_baseline", "hashes": {}},
                )["hashes"][ticker] = baseline["content_hash"]
            listing_columns.append(
                torch.from_numpy(aligned.fill_null(float("nan")).to_numpy().copy())
            )
        columns.append(torch.stack(listing_columns, -1))
    values = (
        torch.stack(columns, -1)
        if columns
        else torch.empty((through // 100, len(tickers), 0), dtype=torch.float64)
    )
    return FeatureBank(atoms, values, evidence)

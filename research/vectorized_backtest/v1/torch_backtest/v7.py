"""V6's fixed nearest-level slots exposed as typed atomic strategy inputs.

The V6 producer owns level selection and causal structural computation. This
adapter only projects its certified [C,2,5,11] bank, binds source provenance,
and joins completed candle clocks into the backtest's prepared envelope.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np
import polars as pl

from research.rl_trading.v1.common import digest
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.features import LEVEL_NAMES, SCALAR_NAMES, CandleFeatures
from research.rl_trading.v6.session_data import open_session
from research.vectorized_backtest.v1.strategy_encoding import AtomicInput, Unit
from research.vectorized_backtest.v1.strategy_encoding.catalog import (
    arte_catalog as base_catalog,
)
from research.vectorized_backtest.v1.strategy_encoding.clickhouse import (
    _clocks,
    certify_source,
)
from research.vectorized_backtest.v1.strategy_encoding.clickhouse import (
    prepare_session as prepare_market,
)
from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError

from .v7_cache import cache_path, load_projection, producer_seals, save_projection

SIDES = ("below", "above")
GEOMETRY = ("center", "lower", "upper")
# Keep raw V6 fields in their original order. Derived units occupy new labels;
# they do not change the meaning of existing labels or the persisted V6 bank.
FIELDS = (
    LEVEL_NAMES
    + tuple(f"{f}_distance_bps" for f in GEOMETRY)
    + tuple(f"{f}_price" for f in GEOMETRY)
)
LEVEL_BASE = 20000
AVAILABILITY = "v7_at_1000"


def arte_catalog(parameters=(), **kwargs):
    """All shared fields plus 170 fixed-slot V7 atoms at completed 1s clocks.

    Role flags and present are Boolean; geometry is return/bps/price. Logged
    counts and age are dimensionless log quantities, not raw counts/seconds.
    Presence itself remains known false for an empty slot. Every other field
    requires presence, preventing V6's zero padding from becoming evidence.
    """
    catalog = base_catalog(parameters, **kwargs)
    atoms = []
    for side_index, side in enumerate(SIDES):
        for slot in range(5):
            prefix = f"v7_{side}_{slot}"
            for field_index, field in enumerate(FIELDS):
                unit = (
                    Unit.BOOLEAN
                    if field in {"present", "historical_origin"}
                    or field.startswith("role_")
                    else Unit.BPS
                    if field.endswith("_bps")
                    else Unit.PRICE
                    if field.endswith("_price")
                    else Unit.RETURN
                    if field.endswith("_rel")
                    else Unit.RATIO
                )
                atoms.append(
                    AtomicInput(
                        LEVEL_BASE + (side_index * 5 + slot) * 32 + field_index,
                        f"v7.{side}[{slot}].{field}",
                        f"{prefix}_{field}",
                        unit,
                        1000,
                        None if field == "present" else f"{prefix}_present",
                        AVAILABILITY,
                        1.0,
                        "v7",
                    )
                )
    return replace(catalog, inputs=catalog.inputs + tuple(atoms))


def attach_level_features(prepared, bank, dependencies, *, certificate):
    """Project a validated V6 bank into one prepared session (input boundary).

    Production callers use prepare_session below, which verifies the complete
    V6 day/previous-day certificates before calling this helper. Tests may pass
    a dummy bank with the same listing() contract. No level generation occurs.
    Only required fields plus their presence masks are materialized on host.
    """
    expected = {f.label: f for f in arte_catalog().inputs if f.source == "v7"}
    requested = tuple(f for f in dependencies if f.source == "v7")
    if any(expected.get(f.label) != f for f in requested):
        raise EncodingError("V7 atom differs from the V6 fixed-slot contract")
    _, origin, start, end = _clocks(prepared.config)
    start = max(
        origin + 14_400_000_000, start - prepared.config.warmup_seconds * 1_000_000
    )
    base = prepared.features[1000]
    needed = {f.column for f in requested} | {
        f.valid_column for f in requested if f.valid_column
    }
    schema = {
        "listing_id": pl.String,
        "time_us": pl.Int64,
        AVAILABILITY: pl.Int64,
        **{name: pl.Float64 for name in needed},
    }
    resident = (
        sum(frame.estimated_size() for frame in prepared.features.values())
        + prepared.broker_bars.estimated_size()
        + prepared.watchlist.estimated_size()
    )
    projected = 0
    rows = []
    # Partition once instead of scanning the entire market frame per listing.
    price_frames = base.select(
        "listing_id", "time_us", "close_int_1000", "price_valid_1000"
    ).partition_by("listing_id", as_dict=True)
    for identity in prepared.watchlist["listing_id"].to_list():
        if isinstance(bank, SessionBank):
            # Full file hashes/census were certified before reaching this path.
            # Validate consumed candles once, without re-reading all full-day
            # feature arrays twice through SessionBank.listing().
            left, right = bank.manifest["offsets"][identity]
            clocks = bank.close_us[left:right]
            if clocks.dtype != np.int64 or np.any(np.diff(clocks) <= 0):
                raise EncodingError("Invalid V6 listing candle ordering")
            lo = int(np.searchsorted(clocks, start, side="left")) + left
            hi = int(np.searchsorted(clocks, end, side="right")) + left
            item = CandleFeatures(
                bank.close_us[lo:hi], bank.scalar[lo:hi], bank.levels[lo:hi]
            )
        else:
            item = bank.listing(identity)
        item.validate()
        take = (item.close_us >= start) & (item.close_us <= end)
        clocks = item.close_us[take]
        projected += len(clocks) * (
            16 + len(identity.encode("utf-8")) + 8 * len(needed)
        )
        if resident + projected > prepared.config.max_prepared_gib * 1024**3:
            raise MemoryError(
                "V7 host projection exceeds the prepared-session memory guard"
            )
        if np.any((clocks - origin) % 1_000_000):
            raise EncodingError("V6 levels need aligned completed 1s candle clocks")
        # [C,2,5,11]: use the producer's below/above and nearest-first ordering.
        levels = np.asarray(item.levels[take], dtype=np.float64)
        present = levels[..., LEVEL_NAMES.index("present")]
        flags = levels[
            ...,
            [
                LEVEL_NAMES.index(f)
                for f in LEVEL_NAMES
                if f.startswith("role_") or f in {"present", "historical_origin"}
            ],
        ]
        if np.any((flags != 0) & (flags != 1)):
            raise EncodingError("V6 presence/role flags must be binary")
        active = present == 1
        if (
            np.any(active & (levels[..., 0] < levels[..., 1]))
            or np.any(active & (levels[..., 0] > levels[..., 2]))
            or np.any(active & (levels[..., 1] <= -1))
        ):
            raise EncodingError("Invalid V6 level band geometry")
        if np.any(active[:, 0] & (levels[:, 0, :, 0] > 0)) or np.any(
            active[:, 1] & (levels[:, 1, :, 0] <= 0)
        ):
            raise EncodingError("V6 level side must agree with completed close")
        # Absolute prices use the matching certified ARTE close, never the
        # current decision price or exp(float32 log_close) as a price authority.
        prices = price_frames.get((identity,), base.head(0)).select(
            "time_us", "close_int_1000", "price_valid_1000"
        )
        bound = pl.DataFrame({"time_us": clocks}).join(
            prices, on="time_us", how="left", validate="1:1"
        )
        if bound["close_int_1000"].null_count():
            raise EncodingError("V6 level candle missing from pinned ARTE bars")
        close = bound["close_int_1000"].to_numpy().astype(np.float64) * 0.0001
        good = bound["price_valid_1000"].fill_null(False).to_numpy().astype(bool)
        stored_close = np.exp(
            item.scalar[take, SCALAR_NAMES.index("log_close")].astype(np.float64)
        )
        if np.any(active.any(axis=(1, 2)) & ~good) or np.any(
            good & ~np.isclose(close, stored_close, rtol=2e-6, atol=1e-6)
        ):
            raise EncodingError("V6 close differs from certified ARTE price evidence")
        columns = {
            "listing_id": [identity] * len(clocks),
            "time_us": clocks,
            AVAILABILITY: clocks,
        }
        for side_index, side in enumerate(SIDES):
            for slot in range(5):
                prefix = f"v7_{side}_{slot}"
                for field_index, field in enumerate(FIELDS):
                    name = f"{prefix}_{field}"
                    if name not in needed:
                        continue
                    value = (
                        levels[:, side_index, slot, field_index]
                        if field_index < len(LEVEL_NAMES)
                        else levels[:, side_index, slot, field_index - len(LEVEL_NAMES)]
                        * 10000
                        if field.endswith("_bps")
                        else close
                        * (
                            1
                            + levels[
                                :, side_index, slot, field_index - len(LEVEL_NAMES) - 3
                            ]
                        )
                    )
                    columns[name] = value
        rows.append(pl.DataFrame(columns, schema=schema))
    projection = pl.concat(rows) if rows else pl.DataFrame(schema=schema)
    merged = base.join(
        projection, on=["listing_id", "time_us"], how="left", validate="1:1"
    )
    # An absent record is unknown; an observed empty slot is known false. Nulls
    # on sparse indicator-only rows are not updates and are carried by the tape.
    proof = {
        "version": "torch-v6-level-atoms-v1",
        "certificate": certificate,
        "fields": [f.label for f in requested],
        "rows": projection.height,
    }
    return replace(
        prepared,
        features={**prepared.features, 1000: merged},
        dependencies=tuple(dict.fromkeys((*prepared.dependencies, *dependencies))),
        source_key=hashlib.sha256(
            json.dumps(
                {"market": prepared.source_key, **proof}, sort_keys=True
            ).encode()
        ).hexdigest(),
        metrics={**prepared.metrics, "v7": proof},
    )


def prepare_session(
    config,
    funnel,
    dependencies,
    *,
    v6_day_root=None,
    v6_previous_root=None,
    v6_runtime_root=None,
    v7_cache=True,
    progress=None,
):
    """Shared ClickHouse funnel plus optional certified V6 level-bank adapter.

    V7 dependencies require explicit runtime session paths. Missing certificates
    fail before database preparation; this consumer never builds V7 products.
    """
    dependencies = tuple(dependencies)
    if not any(f.source == "v7" for f in dependencies):
        return prepare_market(config, funnel, dependencies, progress=progress)
    if v6_day_root is None or v6_runtime_root is None:
        raise EncodingError("V7 inputs require v6_day_root and v6_runtime_root")
    started = perf_counter()
    day, *_ = _clocks(config)
    plan = json.loads((Path(v6_day_root) / "plan.json").read_text(encoding="utf-8"))
    receipt = certify_source(config)
    source = receipt.source
    if (
        plan["day"] != str(day)
        or plan["source_build_id"] != source["build_id"]
        or plan["source_definition_hash"] != source["definition_hash"]
        or plan["source_units_hash"] != digest(source["units"])
    ):
        raise EncodingError(
            "V6 bank and backtest must use the same pinned ARTE day/build/attempts"
        )
    close = next(f for f in base_catalog().inputs if f.name == "close@1000ms")
    market_dependencies = tuple(
        dict.fromkeys((*[f for f in dependencies if f.source != "v7"], close))
    )
    seals = producer_seals(v6_day_root, v6_previous_root, v6_runtime_root)
    folder, key = cache_path(config, funnel, dependencies, seals)
    if v7_cache and (folder / "complete.json").exists():
        prepared = prepare_market(
            config,
            funnel,
            market_dependencies,
            progress=progress,
            source_receipt=receipt,
        )
        read_started = perf_counter()
        prepared = load_projection(folder, key, prepared, dependencies, seals)
        prepared.metrics.update(
            v7_adapter_total_seconds=perf_counter() - started,
            v7_certificate_seconds=0.0,
            v7_projection_seconds=0.0,
            v7_cache_seconds=perf_counter() - read_started,
        )
        if progress:
            progress(
                {"stage": "v7_projection", "status": "verified_copy_reused", "key": key}
            )
        return prepared

    def certify():
        begun = perf_counter()
        if progress:
            progress({"stage": "v7_certificate", "status": "verifying"})
        session = open_session(
            Path(v6_day_root),
            runtime_root=Path(v6_runtime_root),
            previous_root=v6_previous_root,
        )
        seconds = perf_counter() - begun
        if progress:
            progress(
                {"stage": "v7_certificate", "status": "verified", "seconds": seconds}
            )
        return session, seconds

    # Independent read-only lanes, bounded to two workers. Both finish before
    # projection, and no GPU work starts until both authorities have passed.
    with ThreadPoolExecutor(max_workers=2) as workers:
        certification = workers.submit(certify)
        market = workers.submit(
            prepare_market,
            config,
            funnel,
            market_dependencies,
            progress=progress,
            source_receipt=receipt,
        )
        session, certificate_seconds = certification.result()
        prepared = market.result()
    if producer_seals(v6_day_root, v6_previous_root, v6_runtime_root) != seals:
        raise EncodingError("V6 producer seals changed during certification")
    market_key = prepared.source_key
    if progress:
        progress(
            {
                "stage": "v7_projection",
                "status": "projecting",
                "listings": prepared.watchlist.height,
            }
        )
    projection_started = perf_counter()
    prepared = attach_level_features(
        prepared,
        session.bank,
        dependencies,
        certificate=session.source_certificate_sha256,
    )
    if v7_cache:
        save_projection(folder, key, prepared, market_key, seals)
    prepared.metrics["v7_adapter_total_seconds"] = perf_counter() - started
    prepared.metrics["v7_certificate_seconds"] = certificate_seconds
    prepared.metrics["v7_projection_seconds"] = perf_counter() - projection_started
    return prepared

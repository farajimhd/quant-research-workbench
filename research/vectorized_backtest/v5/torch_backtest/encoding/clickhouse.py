"""Read-only certified ClickHouse funnel and bounded, resumable projection.

No market bars cross the wire before watchlist admission is established by SQL.
Only listing identity/certification metadata and first admission records do.
"""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

import polars as pl

from ..source import arte_source
from ..source import arte_sql as sql
from ..source.bracket_source import (
    assert_liquidity_storage,
    broker_attempts,
)

from .catalog import BAR_FIELDS, INDICATOR_FIELDS, RESOLUTIONS
from .config import Funnel, Session
from .core import AtomicInput, EncodingError

NY = ZoneInfo("America/New_York")
VERSION = "atomic-watchlist-window-torch-v2-3-tradable"
_SOURCE_AUTHORITY = object()


@dataclass
class PreparedSession:
    config: Session
    watchlist: pl.DataFrame
    broker_bars: pl.DataFrame
    features: dict[int, pl.DataFrame]
    dependencies: tuple[AtomicInput, ...]
    source_key: str
    metrics: dict


@dataclass(frozen=True)
class CertifiedSource:
    """One authoritative manifest/ledger certification, shared in-process.

    Obtain this through certify_source(), never from a persisted cache. The
    handoff checks paths, day and manifest bytes before reusing the pinned
    attempt snapshot, avoiding a second remote SQLite unit scan.
    """

    manifest: Path
    ledger: Path
    day: date
    manifest_hash: str
    source: dict
    _authority: object = field(default=None, repr=False, compare=False)


def certify_source(config):
    day, *_ = _clocks(config)
    checksum = _hash_file(config.manifest)
    source = arte_source.load_build(config.manifest, config.ledger, [day])
    if _hash_file(config.manifest) != checksum:
        raise EncodingError("Source manifest changed during certification")
    return CertifiedSource(
        Path(config.manifest).resolve(),
        Path(config.ledger).resolve(),
        day,
        checksum,
        source,
        _SOURCE_AUTHORITY,
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def _hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _scope(source, day, names, stage):
    pairs = ",".join(
        f"({sql.literal(name)},toUUID({sql.literal(source['units'][str(day)][name][stage]['attempt_id'])}))"
        for name in names
    )
    return (
        f"build_id={sql.literal(source['build_id'])} AND session_date=toDate({sql.literal(day)}) "
        f"AND (ticker,attempt_id) IN ({pairs})"
    )


def _clocks(config):
    if config.start.tzinfo is None or config.end.tzinfo is None:
        raise EncodingError("Session timestamps must be timezone-aware")
    start, end = config.start.astimezone(NY), config.end.astimezone(NY)
    if start.date() != end.date() or end <= start:
        raise EncodingError("Require one increasing New York session")
    origin = datetime.combine(start.date(), time.min, NY)
    origin_us = int(origin.astimezone(timezone.utc).timestamp() * 1_000_000)
    start_us, end_us = (int(x.timestamp() * 1_000_000) for x in (start, end))
    for resolution in (config.strategy_ms, config.broker_ms):
        if (
            resolution not in RESOLUTIONS
            or (start_us - origin_us) % (resolution * 1000)
            or (end_us - origin_us) % (resolution * 1000)
        ):
            raise EncodingError("Clocks require supported, aligned resolutions")
    if (
        not 14_400_000_000
        <= start_us - origin_us
        < end_us - origin_us
        <= 72_000_000_000
    ):
        raise EncodingError("Session bounds must be within 04:00–20:00 New York")
    if (
        type(config.fetch_tickers) is not int
        or not 1 <= config.fetch_tickers <= 512
        or type(config.fetch_seconds) is not int
        or not 1 <= config.fetch_seconds <= 3600
        or config.fetch_tickers * config.fetch_seconds * 10 > 600_000
        or config.warmup_seconds < 0
        or not math.isfinite(config.max_prepared_gib)
        or config.max_prepared_gib <= 0
    ):
        raise EncodingError("Fetch dimensions/memory/warmup exceed bounded defaults")
    return start.date(), origin_us, start_us, end_us


def _validate_units(reader, source, day, names, progress):
    """Full count/key/content verification; no per-listing network queries."""
    for offset in range(0, len(names), 512):
        group = names[offset : offset + 512]
        for stage, table in arte_source.TABLES.items():
            rows = sql.query(
                reader,
                f"SELECT ticker,count() AS n,uniqExact((resolution_ms,bucket_index)) AS u,"
                f"sum(cityHash64(tuple(*))) AS hash FROM arte.{table} WHERE {_scope(source, day, group, stage)} GROUP BY ticker",
            )
            actual = {row["ticker"]: row for row in rows}
            for name in group:
                saved = source["units"][str(day)][name][stage]
                row = actual.get(name, {"n": 0, "u": 0, "hash": 0})
                if (
                    int(row["n"]) != saved["output_rows"]
                    or int(row["u"]) != int(row["n"])
                    or str(row["hash"]) != saved["output_hash"]
                ):
                    raise EncodingError(
                        f"Persisted certificate mismatch: {name}/{stage}"
                    )
        if progress:
            progress(
                {
                    "stage": "certify",
                    "completed": min(offset + 512, len(names)),
                    "total": len(names),
                }
            )


def _validate_funnel(funnel):
    """Validate even an empty population before issuing any database query."""
    if (
        funnel.signal_ms != 100
        or funnel.admission not in ('squeeze','price_envelope')
        or not all(
            math.isfinite(x)
            for x in (funnel.min_price, funnel.max_price, funnel.impulse_bps)
        )
        or not 0 < funnel.min_price < funnel.max_price
        or funnel.impulse_bps < 0
    ):
        raise EncodingError(
            "Early Squeeze requires supported 100ms impulse and finite bounds"
        )


def admission_sql(source, day, names, funnel, end_offset_us, start_offset_us=14400000000):
    """First price-eligible accepted episode start, computed inside ClickHouse.

    This matches the released completed-bar impulse (5bps, increasing trades and
    volume). A native arrayFold preserves the released 300-second greedy expiry:
    a later impulse inside an existing episode is NOT a new episode start, even
    if price has entered the envelope meanwhile. Membership itself never expires.
    """
    _validate_funnel(funnel)
    scope = _scope(source, day, names, "bars")
    if funnel.admission=='price_envelope':
        # Generic V4 admission: a completed actual 1s price, no fixed strategy
        # impulse/volume/trade predicate. Trading rules supply the signal.
        return ('SELECT ticker,min((toInt64(bucket_index)+1)*1000000) AS admitted_offset_us '
            f'FROM arte.bars_v1 WHERE {scope} AND resolution_ms=1000 AND price_valid=1 '
            f'AND (toInt64(bucket_index)+1)*1000000>={start_offset_us} '
            f'AND (toInt64(bucket_index)+1)*1000000<={end_offset_us} '
            f'AND close_int/10000. BETWEEN {funnel.min_price} AND {funnel.max_price} GROUP BY ticker')
    return (
        "SELECT ticker,tupleElement(episodes,2) AS admitted_offset_us FROM (SELECT ticker,"
        "arrayFold((acc,x)->if(x.1>=acc.1,tuple(x.1+3000,"
        f"if(acc.2=0 AND (x.1+1)*100000>={start_offset_us} AND x.2 BETWEEN {funnel.min_price} AND {funnel.max_price},"
        "(x.1+1)*100000,acc.2)),acc),"
        "arraySort(x->x.1,groupArray(tuple(toInt64(bucket_index),close_int/10000.))),"
        "tuple(toInt64(-1),toInt64(0))) AS episodes "
        "FROM (SELECT * "
        "FROM (SELECT ticker,bucket_index,close_int,volume,trade_count,"
        "lagInFrame(close_int,1,toUInt64(0)) OVER w AS prior_close,"
        "lagInFrame(volume,1,0.) OVER w AS prior_volume,"
        "lagInFrame(trade_count,1,toUInt64(0)) OVER w AS prior_trades "
        f"FROM arte.bars_v1 WHERE {scope} AND resolution_ms=100 AND price_valid=1 "
        f"AND bucket_index>=144000 AND bucket_index<{end_offset_us // 100000} "
        "WINDOW w AS (PARTITION BY ticker ORDER BY bucket_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) "
        f"WHERE prior_close>0 AND (toFloat64(close_int)/prior_close-1)*10000>={funnel.impulse_bps} "
        "AND volume>prior_volume AND trade_count>prior_trades) GROUP BY ticker) "
        "WHERE admitted_offset_us>0"
    )


def prepare_session(
    config: Session,
    funnel: Funnel,
    dependencies: tuple[AtomicInput, ...],
    *,
    progress=None,
    source_receipt=None,
):
    """Cache a fixed upstream dataset; repeated evaluate() calls perform no I/O.

    Supply the union of dependencies for a structural search. A candidate needing
    an unprepared input is rejected rather than substituting a stale/default value.
    """
    started = perf_counter()
    _validate_funnel(funnel)
    day, origin_us, start_us, end_us = _clocks(config)
    if not config.runtime.parent.is_dir():
        raise FileNotFoundError("Required runtime parent unavailable")
    config.runtime.mkdir(exist_ok=True)
    receipt = certify_source(config) if source_receipt is None else source_receipt
    if (
        not isinstance(receipt, CertifiedSource)
        or receipt._authority is not _SOURCE_AUTHORITY
        or receipt.manifest != Path(config.manifest).resolve()
        or receipt.ledger != Path(config.ledger).resolve()
        or receipt.day != day
        or _hash_file(config.manifest) != receipt.manifest_hash
    ):
        raise EncodingError("Certified source receipt differs from session inputs")
    source = receipt.source
    if len(set(config.excluded_tickers)) != len(config.excluded_tickers) or any(
            not isinstance(t, str) or not t or t != t.strip().upper() for t in config.excluded_tickers):
        raise EncodingError("Exclusions require unique, nonempty uppercase ticker names")
    names = sorted(set(source["units"][str(day)]) - set(config.excluded_tickers))
    if not names:
        raise EncodingError("Research exclusions leave no tradable population")
    broker_attempts(source, config.ledger, day, set(names))
    signature = {
        "version": VERSION,
        "source": source,
        "manifest": receipt.manifest_hash,
        "session": asdict(config),
        "funnel": asdict(funnel),
        "dependencies": [asdict(x) for x in dependencies],
    }
    key = _digest(signature)
    cache = config.runtime / key
    cache.mkdir(exist_ok=True)
    complete = cache / "complete.json"
    checkpoint = cache / "progress.json"
    records = (
        json.loads(checkpoint.read_text())
        if checkpoint.exists()
        else {"key": key, "files": {}}
    )
    if records["key"] != key:
        raise EncodingError("Cache checkpoint identity mismatch")
    with closing(sql.ArteReader(threads=2)) as reader:
        arte_source.storage_check(reader)
        assert_liquidity_storage(reader)
        if progress:
            progress({"stage": "Certify population identity", "message": "Checking pinned preopen identity snapshot and content hash"})
        members, population = arte_source.population(reader, source, day, diagnostic_directory=cache,
                                                     excluded_tickers=config.excluded_tickers,
                                                     regular_us_exchanges_only=config.regular_us_exchanges_only)
        names = sorted({row['ticker'] for row in members})
        if not names:
            raise EncodingError('No eligible stock exchange listings in pinned population')
        if progress:
            progress({"stage": "Tradable population selected", "completed": len(members), "total": len(names),
                      "message": f"{len(members):,} certified tradable tickers; explicit exclusions: {', '.join(config.excluded_tickers) or 'none'}"})
        identities = pl.DataFrame(members).select(
            "ticker", pl.col("listing_id").cast(pl.String)
        )
        if complete.exists():
            seal = json.loads(complete.read_text())
            if seal["key"] != key:
                raise EncodingError("Complete cache identity mismatch")
            for item in seal["files"].values():
                if _hash_file(cache / item["file"]) != item["sha256"]:
                    raise EncodingError("Corrupt completed cache")
            records = seal
            watchlist = pl.read_parquet(cache / "watchlist.parquet")
            candidate_count = seal.get("candidate_count")
        else:
            if progress:
                progress({"stage": "certify", "completed": 0, "total": len(names)})
            _validate_units(reader, source, day, names, progress)
            # Cheap necessary-condition scan before expensive lag/episode work.
            # An eventual candidate is not active early: admission clocks below
            # remain the only watchlist authority. Keep the full prefix for its
            # episode context, even outside the price band.
            if progress:
                progress({"stage": "Price candidate scan", "completed": 0, "total": len(names),
                          "message": ("Scanning certified completed 1s prices" if funnel.admission=='price_envelope'
                                      else "Scanning certified 100ms prices before squeeze admission")})
            candidates = []
            for offset in range(0, len(names), 512):
                group = names[offset : offset + 512]
                candidate_rows = sql.query(
                    reader,
                    "SELECT DISTINCT ticker FROM arte.bars_v1 "
                    f"WHERE {_scope(source, day, group, 'bars')} AND resolution_ms={1000 if funnel.admission=='price_envelope' else 100} AND price_valid=1 "
                    f"AND bucket_index>={14400 if funnel.admission=='price_envelope' else 144000} AND bucket_index<{(end_us - origin_us) // (1000000 if funnel.admission=='price_envelope' else 100000)} "
                    f"AND close_int/10000. BETWEEN {funnel.min_price} AND {funnel.max_price}",
                )
                candidates.extend(row["ticker"] for row in candidate_rows)
                if progress:
                    progress({"stage": "Price candidate scan", "completed": min(offset+512, len(names)), "total": len(names)})
            candidate_count = len(candidates)
            admission = []
            if progress:
                progress({"stage": "Price-envelope admission" if funnel.admission=='price_envelope' else "Detect squeeze admission",
                          "completed": 0, "total": len(candidates),
                          "message": ("Finding first eligible completed candle in the selected window" if funnel.admission=='price_envelope'
                                      else "Evaluating causal squeeze episodes within the selected window")})
            for offset in range(0, len(candidates), 512):
                admission.extend(
                    sql.query(
                        reader,
                        admission_sql(
                            source,
                            day,
                            candidates[offset : offset + 512],
                            funnel,
                            end_us - origin_us,
                            start_us - origin_us,
                        ),
                    )
                )
                if progress:
                    progress({"stage": "Price-envelope admission" if funnel.admission=='price_envelope' else "Detect squeeze admission",
                              "completed": min(offset+512, len(candidates)), "total": len(candidates)})
            watchlist = pl.DataFrame(
                admission, schema={"ticker": pl.String, "admitted_offset_us": pl.Int64}
            )
            watchlist = (
                watchlist.join(identities, on="ticker", validate="1:1")
                .with_columns(
                    (pl.col("admitted_offset_us") + origin_us).alias("admitted_at_us")
                )
                .drop("admitted_offset_us")
            )
            path = cache / "watchlist.parquet"
            watchlist.write_parquet(path)
            records["files"]["watchlist"] = {
                "file": path.name,
                "sha256": _hash_file(path),
                "rows": watchlist.height,
            }
        selected = sorted(watchlist["ticker"].to_list())
        # Carry the complete pinned security identity into opening-as-of
        # reference reads, including when reusing an older private watchlist
        # cache. Join on both ticker and listing to reject identity substitution.
        reference_identity=pl.DataFrame(members).select(
            'ticker',pl.col('listing_id').cast(pl.String),
            pl.col('symbol_id').cast(pl.String),pl.col('security_id').cast(pl.String))
        watchlist=watchlist.join(reference_identity,on=['ticker','listing_id'],how='left',validate='1:1')
        if watchlist.select(pl.any_horizontal(pl.col('symbol_id').is_null(),pl.col('security_id').is_null()).any()).item():
            raise EncodingError('Watchlist lacks pinned security identity')
        if progress:
            progress(
                {
                    "stage": "funnel",
                    "population": len(names),
                    "price_candidates": candidate_count,
                    "watchlist": len(selected),
                }
            )

        def fetch(statement, name, schema):
            """Atomic checkpoint after each bounded, validated response."""
            path = cache / f"{name}.parquet"
            if name in records["files"]:
                if _hash_file(path) != records["files"][name]["sha256"]:
                    raise EncodingError("Corrupt partial cache")
                return pl.read_parquet(path).with_columns(
                    *[
                        pl.col(column).cast(dtype, strict=True)
                        for column, dtype in schema.items()
                    ]
                )
            # Source types are authoritative: eligible volume can be fractional,
            # and initial zero/integer rows must not infer an integer column.
            data = arte_source.frame(reader, statement, schema)
            temporary = path.with_suffix(".tmp")
            data.write_parquet(temporary)
            temporary.replace(path)
            records["files"][name] = {
                "file": path.name,
                "sha256": _hash_file(path),
                "rows": data.height,
            }
            temporary = checkpoint.with_suffix(".tmp")
            temporary.write_text(json.dumps(records, indent=2))
            temporary.replace(checkpoint)
            return data

        projections = {}
        for feature in dependencies:
            if feature.source == "state":
                continue
            if feature.resolution_ms not in RESOLUTIONS or feature.source not in {
                "bars",
                "indicators",
            }:
                raise EncodingError("Input needs an unsupported data adapter")
            key_projection = (feature.source, feature.resolution_ms)
            columns = projections.setdefault(key_projection, set())
            suffix = f"_{feature.resolution_ms}"
            columns.add(feature.column.removesuffix(suffix))
            if feature.valid_column and not feature.valid_column.startswith(
                "indicator_valid"
            ):
                columns.add(feature.valid_column.removesuffix(suffix))
        # Execution evidence is independent of strategy predicate dependencies.
        projections.setdefault(("bars", config.broker_ms), set()).update(
            ("close_int", "price_valid", "execution_volume", "execution_notional")
        )
        lanes = {}
        resident_bytes = 0
        for (lane, resolution), columns in sorted(projections.items()):
            allowed = (
                {physical for physical, _, _ in BAR_FIELDS.values()}
                | {"price_valid", "extremes_valid"}
                if lane == "bars"
                else set(INDICATOR_FIELDS)
            )
            if not columns <= allowed:
                raise EncodingError("Projection is not a persisted ARTE field")
            # Fetch a complete pre-admission prefix for causal temporal windows.
            # Market-wide decision rows are never synthesized for all 6100 slots.
            lower_us = max(
                origin_us + 14_400_000_000, start_us - config.warmup_seconds * 1_000_000
            )
            frames = []
            for offset in range(0, len(selected), config.fetch_tickers):
                group = selected[offset : offset + config.fetch_tickers]
                stage = "bars" if lane == "bars" else "technical"
                table = arte_source.TABLES[stage]
                where = _scope(source, day, group, stage)
                expected = int(
                    sql.query(
                        reader,
                        f"SELECT count() AS n FROM arte.{table} WHERE {where} AND resolution_ms={resolution} "
                        f"AND bucket_index>={(lower_us - origin_us) // (resolution * 1000)} "
                        f"AND bucket_index<{(end_us - origin_us) // (resolution * 1000)}",
                    )[0]["n"]
                )
                # Sparse sessions can fit a whole listing group in one response.
                # Dense groups fall back to bounded clock chunks. This avoids
                # thousands of tiny queries without weakening reader limits.
                row_budget = min(150_000, 80_000_000 // (64 + 32 * len(columns)))
                seconds = max(
                    1,
                    min(
                        config.fetch_seconds,
                        row_budget * resolution // (1000 * len(group)),
                    ),
                )
                chunk_us = (
                    end_us - lower_us if expected <= row_budget else seconds * 1_000_000
                )
                group_rows = 0
                for lo in range(lower_us, end_us, chunk_us):
                    hi = min(end_us, lo + chunk_us)
                    # Boundaries, not starts: never include an unfinished coarse bar.
                    first = (lo - origin_us) // (resolution * 1000)
                    last = (hi - origin_us) // (resolution * 1000)
                    boundary = f"toInt64({origin_us})+(toInt64(bucket_index)+1)*{resolution * 1000}"
                    selection = ",".join(
                        f"{name} AS {name}_{resolution}" for name in sorted(columns)
                    )
                    statement = (
                        f"SELECT ticker,{boundary} AS time_us,{selection} FROM arte.{table} "
                        f"WHERE {_scope(source, day, group, stage)} AND resolution_ms={resolution} "
                        f"AND bucket_index>={first} AND bucket_index<{last} ORDER BY ticker,bucket_index"
                    )
                    tag = f"{lane}-{resolution}-{offset}-{lo}-{hi}"
                    schema = {"ticker": pl.String, "time_us": pl.Int64}
                    for column in columns:
                        integer = column.endswith("_int") or column in {
                            "price_valid",
                            "extremes_valid",
                            "trade_count",
                            "sample_count",
                            "rsi_ready",
                            "atr_ready",
                        }
                        schema[f"{column}_{resolution}"] = (
                            pl.Int64 if integer else pl.Float64
                        )
                    data = fetch(statement, tag, schema)
                    if data.height > row_budget:
                        raise EncodingError(
                            "Fetched response exceeds planned row budget"
                        )
                    group_rows += data.height
                    if (
                        data.select(pl.struct("ticker", "time_us").n_unique()).item()
                        != data.height
                    ):
                        raise EncodingError("Duplicate fetched source key")
                    data = (
                        data.join(identities, on="ticker", validate="m:1")
                        .drop("ticker")
                        .with_columns(pl.col("time_us").cast(pl.Int64))
                    )
                    if data["listing_id"].null_count():
                        raise EncodingError("Missing identity")
                    resident_bytes += data.estimated_size()
                    if resident_bytes > config.max_prepared_gib * 1024**3:
                        raise MemoryError("Prepared session exceeds memory bound")
                    frames.append(data)
                    if progress:
                        progress(
                            {
                                "stage": "fetch",
                                "lane": lane,
                                "resolution_ms": resolution,
                                "rows": data.height,
                                "completed": group_rows,
                                "total": expected,
                                "unit": "rows in ticker group",
                            }
                        )
                if group_rows != expected:
                    raise EncodingError(
                        "Projection count differs from requested certified range"
                    )
            lanes[(lane, resolution)] = (
                pl.concat(frames)
                if frames
                else pl.DataFrame(
                    schema={
                        "listing_id": pl.String,
                        "time_us": pl.Int64,
                        **{f"{c}_{resolution}": pl.Float64 for c in columns},
                    }
                )
            )
        features = {}
        for resolution in sorted({r for _, r in projections}):
            parts = []
            for lane in ("bars", "indicators"):
                if (lane, resolution) not in lanes:
                    continue
                data = lanes[(lane, resolution)]
                data = data.with_columns(
                    pl.col("time_us").alias(
                        f"{'bar' if lane == 'bars' else 'indicator'}_at_{resolution}"
                    )
                )
                if lane == "indicators":
                    data = data.with_columns(
                        pl.lit(True).alias(f"indicator_valid_{resolution}")
                    )
                parts.append(data)
            features[resolution] = (
                parts[0]
                if len(parts) == 1
                else parts[0].join(
                    parts[1],
                    on=["listing_id", "time_us"],
                    how="full",
                    coalesce=True,
                    validate="1:1",
                )
            )
        raw = lanes[("bars", config.broker_ms)]
        broker = raw.select(
            "listing_id",
            "time_us",
            (pl.col(f"close_int_{config.broker_ms}").cast(pl.Float64) / 10000).alias(
                "close"
            ),
            pl.col(f"price_valid_{config.broker_ms}").cast(pl.Boolean).alias("valid"),
            pl.col(f"execution_volume_{config.broker_ms}")
            .cast(pl.Float64)
            .alias("volume"),
            pl.col(f"execution_notional_{config.broker_ms}")
            .cast(pl.Float64)
            .alias("notional"),
        )
        if broker.filter(
            ~pl.all_horizontal(pl.col("close", "volume", "notional").is_finite())
            | (pl.col("volume") < 0)
            | (pl.col("notional") < 0)
            | ((pl.col("volume") > 0) & (pl.col("notional") <= 0))
        ).height:
            raise EncodingError("Invalid broker evidence")
        broker = broker.with_columns(
            pl.when(pl.col("volume") > 0)
            .then(pl.col("notional") / pl.col("volume"))
            .otherwise(None)
            .alias("vwap")
        )
        seal = {
            "key": key,
            "files": records["files"],
            "signature": signature,
            "candidate_count": candidate_count,
        }
        temporary = complete.with_suffix(".tmp")
        temporary.write_text(json.dumps(seal, default=str, indent=2))
        temporary.replace(complete)
    metrics = {
        "population": len(names),
        "eligibility": population['eligibility'],
        "price_candidates": candidate_count,
        "watchlist": watchlist.height,
        "broker_rows": broker.height,
        "resident_mib": resident_bytes / 1024**2,
        "preparation_seconds": perf_counter() - started,
    }
    return PreparedSession(
        config, watchlist, broker, features, dependencies, key, metrics
    )

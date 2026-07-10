from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


RUST_CRATE_DIR = Path(__file__).resolve().parent / "rust_chrono_loader"


@dataclass(slots=True)
class RustQueueRuntimeConfig:
    ticker_count: int = 8_000
    origins_per_ticker: int = 512
    event_stream_len: int = 1_024
    event_feature_count: int = 25
    batch_size: int = 1_024
    realtime_read_workers: int = 32
    prefetch_read_workers: int = 16
    realtime_process_workers: int = 32
    prefetch_process_workers: int = 16
    prefetch_ticker_count: int = 4_000
    read_sleep_us: int = 0
    process_sleep_us: int = 0


@dataclass(slots=True)
class RustQueueRuntimeStats:
    status: int
    elapsed_ns: int
    read_jobs_enqueued: int
    read_jobs_finished: int
    process_jobs_enqueued: int
    process_jobs_finished: int
    realtime_read_jobs: int
    prefetch_read_jobs: int
    realtime_process_jobs: int
    prefetch_process_jobs: int
    read_priority_steals: int
    process_priority_steals: int
    read_worker_ns: int
    process_worker_ns: int
    samples: int
    batches: int
    cache_tickers: int
    event_cache_rebuilds: int
    event_cache_appends: int
    event_cache_reused: int
    bytes_allocated: int
    checksum_bits: int

    @property
    def elapsed_seconds(self) -> float:
        return float(self.elapsed_ns) / 1_000_000_000.0

    @property
    def samples_per_second(self) -> float:
        return float(self.samples) / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def batches_per_second(self) -> float:
        return float(self.batches) / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def allocated_gib(self) -> float:
        return float(self.bytes_allocated) / float(1024**3)

    @property
    def read_worker_seconds(self) -> float:
        return float(self.read_worker_ns) / 1_000_000_000.0

    @property
    def process_worker_seconds(self) -> float:
        return float(self.process_worker_ns) / 1_000_000_000.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["elapsed_seconds"] = self.elapsed_seconds
        out["samples_per_second"] = self.samples_per_second
        out["batches_per_second"] = self.batches_per_second
        out["allocated_gib"] = self.allocated_gib
        out["read_worker_seconds"] = self.read_worker_seconds
        out["process_worker_seconds"] = self.process_worker_seconds
        return out


class _FfiConfig(ctypes.Structure):
    _fields_ = [
        ("ticker_count", ctypes.c_uint32),
        ("origins_per_ticker", ctypes.c_uint32),
        ("event_stream_len", ctypes.c_uint32),
        ("event_feature_count", ctypes.c_uint32),
        ("batch_size", ctypes.c_uint32),
        ("realtime_read_workers", ctypes.c_uint32),
        ("prefetch_read_workers", ctypes.c_uint32),
        ("realtime_process_workers", ctypes.c_uint32),
        ("prefetch_process_workers", ctypes.c_uint32),
        ("prefetch_ticker_count", ctypes.c_uint32),
        ("read_sleep_us", ctypes.c_uint32),
        ("process_sleep_us", ctypes.c_uint32),
    ]


class _FfiStats(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("elapsed_ns", ctypes.c_uint64),
        ("read_jobs_enqueued", ctypes.c_uint64),
        ("read_jobs_finished", ctypes.c_uint64),
        ("process_jobs_enqueued", ctypes.c_uint64),
        ("process_jobs_finished", ctypes.c_uint64),
        ("realtime_read_jobs", ctypes.c_uint64),
        ("prefetch_read_jobs", ctypes.c_uint64),
        ("realtime_process_jobs", ctypes.c_uint64),
        ("prefetch_process_jobs", ctypes.c_uint64),
        ("read_priority_steals", ctypes.c_uint64),
        ("process_priority_steals", ctypes.c_uint64),
        ("read_worker_ns", ctypes.c_uint64),
        ("process_worker_ns", ctypes.c_uint64),
        ("samples", ctypes.c_uint64),
        ("batches", ctypes.c_uint64),
        ("cache_tickers", ctypes.c_uint64),
        ("event_cache_rebuilds", ctypes.c_uint64),
        ("event_cache_appends", ctypes.c_uint64),
        ("event_cache_reused", ctypes.c_uint64),
        ("bytes_allocated", ctypes.c_uint64),
        ("checksum_bits", ctypes.c_uint64),
    ]


def rust_library_path(*, release: bool = True) -> Path:
    suffix = {"Windows": ".dll", "Linux": ".so", "Darwin": ".dylib"}.get(platform.system(), ".dll")
    prefix = "" if platform.system() == "Windows" else "lib"
    profile = "release" if release else "debug"
    return RUST_CRATE_DIR / "target" / profile / f"{prefix}rolling_loader_rust{suffix}"


def build_rust_library(*, release: bool = True) -> Path:
    command = ["cargo", "build", "--manifest-path", str(RUST_CRATE_DIR / "Cargo.toml")]
    if release:
        command.append("--release")
    subprocess.run(command, check=True)
    path = rust_library_path(release=release)
    if not path.exists():
        raise FileNotFoundError(f"Rust rolling loader library was not built: {path}")
    return path


def load_rust_library(path: str | os.PathLike[str] | None = None, *, build_if_missing: bool = True, release: bool = True) -> ctypes.CDLL:
    lib_path = Path(path) if path is not None else rust_library_path(release=release)
    if not lib_path.exists() and build_if_missing:
        lib_path = build_rust_library(release=release)
    if not lib_path.exists():
        raise FileNotFoundError(f"Rust rolling loader library not found: {lib_path}")
    lib = ctypes.CDLL(str(lib_path))
    lib.rolling_loader_rust_profile.argtypes = [ctypes.POINTER(_FfiConfig), ctypes.POINTER(_FfiStats)]
    lib.rolling_loader_rust_profile.restype = ctypes.c_int32
    if hasattr(lib, "rolling_loader_rust_profile_real_cache"):
        lib.rolling_loader_rust_profile_real_cache.argtypes = [
            ctypes.POINTER(_FfiRealCacheConfig),
            ctypes.POINTER(_FfiRealCachePart),
            ctypes.POINTER(_FfiRealCacheStats),
        ]
        lib.rolling_loader_rust_profile_real_cache.restype = ctypes.c_int32
    if hasattr(lib, "rolling_loader_rust_assemble_tensors"):
        lib.rolling_loader_rust_assemble_tensors.argtypes = [
            ctypes.POINTER(_FfiTensorAssemblyConfig),
            ctypes.POINTER(_FfiTensorAssemblySpec),
            ctypes.POINTER(_FfiTensorAssemblyStats),
        ]
        lib.rolling_loader_rust_assemble_tensors.restype = ctypes.c_int32
    if hasattr(lib, "rolling_loader_rust_profile_native_cache"):
        lib.rolling_loader_rust_profile_native_cache.argtypes = [
            ctypes.POINTER(_FfiNativeCacheProfileConfig),
            ctypes.POINTER(_FfiNativeCacheProfileStats),
        ]
        lib.rolling_loader_rust_profile_native_cache.restype = ctypes.c_int32
    lib.rolling_loader_rust_version.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.rolling_loader_rust_version.restype = ctypes.c_size_t
    return lib


def rust_version(lib: ctypes.CDLL | None = None) -> str:
    lib = lib or load_rust_library()
    buffer = ctypes.create_string_buffer(256)
    lib.rolling_loader_rust_version(buffer, ctypes.sizeof(buffer))
    return buffer.value.decode("utf-8", errors="replace")


def profile_rust_queue_runtime(
    config: RustQueueRuntimeConfig | None = None,
    *,
    library_path: str | os.PathLike[str] | None = None,
    build_if_missing: bool = True,
    release: bool = True,
) -> RustQueueRuntimeStats:
    config = config or RustQueueRuntimeConfig()
    ffi_config = _FfiConfig(**asdict(config))
    ffi_stats = _FfiStats()
    lib = load_rust_library(library_path, build_if_missing=build_if_missing, release=release)
    status = int(lib.rolling_loader_rust_profile(ctypes.byref(ffi_config), ctypes.byref(ffi_stats)))
    if status != 0:
        raise RuntimeError(f"rolling_loader_rust_profile failed with status={status}")
    payload = {name: int(getattr(ffi_stats, name)) for name, _ctype in _FfiStats._fields_}
    return RustQueueRuntimeStats(**payload)


def profile_to_json(config: RustQueueRuntimeConfig, stats: RustQueueRuntimeStats) -> str:
    return json.dumps({"config": asdict(config), "stats": stats.to_dict()}, sort_keys=True)


@dataclass(slots=True)
class RustRealCacheRuntimeConfig:
    part_count: int
    event_stream_len: int = 1_024
    batch_size: int = 1_024
    realtime_process_workers: int = 32
    prefetch_process_workers: int = 16


@dataclass(slots=True)
class RustRealCacheRuntimeStats:
    status: int
    elapsed_ns: int
    process_jobs_enqueued: int
    process_jobs_finished: int
    realtime_process_jobs: int
    prefetch_process_jobs: int
    process_priority_steals: int
    process_worker_ns: int
    parts: int
    event_rows: int
    origins_seen: int
    samples: int
    batches: int
    invalid_origins: int
    ordinal_mismatches: int
    event_cache_rebuilds: int
    event_cache_appends: int
    event_cache_reused: int
    bytes_input: int
    checksum_bits: int

    @property
    def elapsed_seconds(self) -> float:
        return float(self.elapsed_ns) / 1_000_000_000.0

    @property
    def process_worker_seconds(self) -> float:
        return float(self.process_worker_ns) / 1_000_000_000.0

    @property
    def samples_per_second(self) -> float:
        return float(self.samples) / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def batches_per_second(self) -> float:
        return float(self.batches) / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def input_gib(self) -> float:
        return float(self.bytes_input) / float(1024**3)

    @property
    def valid_origin_fraction(self) -> float:
        return float(self.samples) / float(self.origins_seen) if self.origins_seen else 0.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["elapsed_seconds"] = self.elapsed_seconds
        out["process_worker_seconds"] = self.process_worker_seconds
        out["samples_per_second"] = self.samples_per_second
        out["batches_per_second"] = self.batches_per_second
        out["input_gib"] = self.input_gib
        out["valid_origin_fraction"] = self.valid_origin_fraction
        return out


@dataclass(slots=True)
class RustRealCachePart:
    ticker_id: int
    ordinals: Any
    features: Any
    origin_offsets: Any
    origin_ordinals: Any
    priority: int = 0
    label: str = ""


@dataclass(slots=True)
class RustTensorAssemblyConfig:
    tensor_count: int = 0
    realtime_workers: int = 32
    prefetch_workers: int = 16


@dataclass(slots=True)
class RustTensorAssemblyStats:
    status: int
    elapsed_ns: int
    jobs_enqueued: int
    jobs_finished: int
    realtime_jobs: int
    prefetch_jobs: int
    priority_steals: int
    worker_ns: int
    tensors: int
    rows_copied: int
    bytes_copied: int
    contiguous_tensors: int
    gathered_tensors: int
    invalid_specs: int
    checksum_bits: int

    @property
    def elapsed_seconds(self) -> float:
        return float(self.elapsed_ns) / 1_000_000_000.0

    @property
    def worker_seconds(self) -> float:
        return float(self.worker_ns) / 1_000_000_000.0

    @property
    def gib_copied(self) -> float:
        return float(self.bytes_copied) / float(1024**3)

    @property
    def gib_per_second(self) -> float:
        return self.gib_copied / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["elapsed_seconds"] = self.elapsed_seconds
        out["worker_seconds"] = self.worker_seconds
        out["gib_copied"] = self.gib_copied
        out["gib_per_second"] = self.gib_per_second
        return out


@dataclass(slots=True)
class RustTensorAssemblyResult:
    stats: RustTensorAssemblyStats
    outputs: dict[str, Any]


@dataclass(slots=True)
class RustNativeCacheProfileConfig:
    cache_root: Path
    month: str = "2019-02"
    ticker_limit: int = 64
    batch_size: int = 1024
    max_batches: int = 8
    event_stream_len: int = 1024
    read_workers: int = 8
    strict: bool = True


@dataclass(slots=True)
class RustNativeCacheProfileStats:
    status: int
    elapsed_ns: int
    packages_discovered: int
    packages_processed: int
    parts_processed: int
    parquet_files_opened: int
    parquet_rows_seen: int
    event_rows: int
    origin_rows: int
    samples: int
    batches: int
    invalid_event_windows: int
    ordinal_mismatches: int
    ticker_news_rows: int
    market_news_rows: int
    sec_filing_rows: int
    xbrl_rows: int
    corporate_action_rows: int
    ticker_daily_bar_rows: int
    global_daily_bar_rows: int
    intraday_base_bar_rows: int
    scanner_rows: int
    text_selected: int
    xbrl_selected: int
    corporate_action_selected: int
    ticker_daily_bar_selected: int
    global_daily_bar_selected: int
    scanner_dates_touched: int
    schema_errors: int
    io_errors: int
    read_ns: int
    event_ns: int
    context_ns: int
    checksum_bits: int

    @property
    def elapsed_seconds(self) -> float:
        return float(self.elapsed_ns) / 1_000_000_000.0

    @property
    def read_seconds(self) -> float:
        return float(self.read_ns) / 1_000_000_000.0

    @property
    def event_seconds(self) -> float:
        return float(self.event_ns) / 1_000_000_000.0

    @property
    def context_seconds(self) -> float:
        return float(self.context_ns) / 1_000_000_000.0

    @property
    def samples_per_second(self) -> float:
        return float(self.samples) / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def batches_per_second(self) -> float:
        return float(self.batches) / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["elapsed_seconds"] = self.elapsed_seconds
        out["read_seconds"] = self.read_seconds
        out["event_seconds"] = self.event_seconds
        out["context_seconds"] = self.context_seconds
        out["samples_per_second"] = self.samples_per_second
        out["batches_per_second"] = self.batches_per_second
        return out


class _FfiNativeCacheProfileConfig(ctypes.Structure):
    _fields_ = [
        ("cache_root", ctypes.c_char_p),
        ("month", ctypes.c_char_p),
        ("ticker_limit", ctypes.c_uint32),
        ("batch_size", ctypes.c_uint32),
        ("max_batches", ctypes.c_uint32),
        ("event_stream_len", ctypes.c_uint32),
        ("read_workers", ctypes.c_uint32),
        ("strict", ctypes.c_uint32),
    ]


class _FfiNativeCacheProfileStats(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("elapsed_ns", ctypes.c_uint64),
        ("packages_discovered", ctypes.c_uint64),
        ("packages_processed", ctypes.c_uint64),
        ("parts_processed", ctypes.c_uint64),
        ("parquet_files_opened", ctypes.c_uint64),
        ("parquet_rows_seen", ctypes.c_uint64),
        ("event_rows", ctypes.c_uint64),
        ("origin_rows", ctypes.c_uint64),
        ("samples", ctypes.c_uint64),
        ("batches", ctypes.c_uint64),
        ("invalid_event_windows", ctypes.c_uint64),
        ("ordinal_mismatches", ctypes.c_uint64),
        ("ticker_news_rows", ctypes.c_uint64),
        ("market_news_rows", ctypes.c_uint64),
        ("sec_filing_rows", ctypes.c_uint64),
        ("xbrl_rows", ctypes.c_uint64),
        ("corporate_action_rows", ctypes.c_uint64),
        ("ticker_daily_bar_rows", ctypes.c_uint64),
        ("global_daily_bar_rows", ctypes.c_uint64),
        ("intraday_base_bar_rows", ctypes.c_uint64),
        ("scanner_rows", ctypes.c_uint64),
        ("text_selected", ctypes.c_uint64),
        ("xbrl_selected", ctypes.c_uint64),
        ("corporate_action_selected", ctypes.c_uint64),
        ("ticker_daily_bar_selected", ctypes.c_uint64),
        ("global_daily_bar_selected", ctypes.c_uint64),
        ("scanner_dates_touched", ctypes.c_uint64),
        ("schema_errors", ctypes.c_uint64),
        ("io_errors", ctypes.c_uint64),
        ("read_ns", ctypes.c_uint64),
        ("event_ns", ctypes.c_uint64),
        ("context_ns", ctypes.c_uint64),
        ("checksum_bits", ctypes.c_uint64),
    ]


class _FfiRealCacheConfig(ctypes.Structure):
    _fields_ = [
        ("part_count", ctypes.c_uint32),
        ("event_stream_len", ctypes.c_uint32),
        ("batch_size", ctypes.c_uint32),
        ("realtime_process_workers", ctypes.c_uint32),
        ("prefetch_process_workers", ctypes.c_uint32),
    ]


class _FfiRealCachePart(ctypes.Structure):
    _fields_ = [
        ("ticker_id", ctypes.c_uint64),
        ("event_rows", ctypes.c_uint64),
        ("origin_count", ctypes.c_uint64),
        ("feature_count", ctypes.c_uint32),
        ("priority", ctypes.c_uint32),
        ("ordinals", ctypes.POINTER(ctypes.c_uint64)),
        ("features", ctypes.POINTER(ctypes.c_float)),
        ("origin_offsets", ctypes.POINTER(ctypes.c_int64)),
        ("origin_ordinals", ctypes.POINTER(ctypes.c_uint64)),
    ]


class _FfiRealCacheStats(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("elapsed_ns", ctypes.c_uint64),
        ("process_jobs_enqueued", ctypes.c_uint64),
        ("process_jobs_finished", ctypes.c_uint64),
        ("realtime_process_jobs", ctypes.c_uint64),
        ("prefetch_process_jobs", ctypes.c_uint64),
        ("process_priority_steals", ctypes.c_uint64),
        ("process_worker_ns", ctypes.c_uint64),
        ("parts", ctypes.c_uint64),
        ("event_rows", ctypes.c_uint64),
        ("origins_seen", ctypes.c_uint64),
        ("samples", ctypes.c_uint64),
        ("batches", ctypes.c_uint64),
        ("invalid_origins", ctypes.c_uint64),
        ("ordinal_mismatches", ctypes.c_uint64),
        ("event_cache_rebuilds", ctypes.c_uint64),
        ("event_cache_appends", ctypes.c_uint64),
        ("event_cache_reused", ctypes.c_uint64),
        ("bytes_input", ctypes.c_uint64),
        ("checksum_bits", ctypes.c_uint64),
    ]


class _FfiTensorAssemblyConfig(ctypes.Structure):
    _fields_ = [
        ("tensor_count", ctypes.c_uint32),
        ("realtime_workers", ctypes.c_uint32),
        ("prefetch_workers", ctypes.c_uint32),
    ]


class _FfiTensorAssemblySpec(ctypes.Structure):
    _fields_ = [
        ("source", ctypes.c_void_p),
        ("dest", ctypes.c_void_p),
        ("row_indices", ctypes.c_void_p),
        ("rows", ctypes.c_uint64),
        ("source_rows", ctypes.c_uint64),
        ("row_width_bytes", ctypes.c_uint64),
        ("priority", ctypes.c_uint32),
    ]


class _FfiTensorAssemblyStats(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("elapsed_ns", ctypes.c_uint64),
        ("jobs_enqueued", ctypes.c_uint64),
        ("jobs_finished", ctypes.c_uint64),
        ("realtime_jobs", ctypes.c_uint64),
        ("prefetch_jobs", ctypes.c_uint64),
        ("priority_steals", ctypes.c_uint64),
        ("worker_ns", ctypes.c_uint64),
        ("tensors", ctypes.c_uint64),
        ("rows_copied", ctypes.c_uint64),
        ("bytes_copied", ctypes.c_uint64),
        ("contiguous_tensors", ctypes.c_uint64),
        ("gathered_tensors", ctypes.c_uint64),
        ("invalid_specs", ctypes.c_uint64),
        ("checksum_bits", ctypes.c_uint64),
    ]


def _as_contiguous_numpy(array: Any, *, dtype: str) -> Any:
    import numpy as np

    out = np.asarray(array, dtype=np.dtype(dtype), order="C")
    if not out.flags["C_CONTIGUOUS"]:
        out = np.ascontiguousarray(out)
    return out


def profile_rust_real_cache_parts(
    parts: list[RustRealCachePart],
    config: RustRealCacheRuntimeConfig | None = None,
    *,
    library_path: str | os.PathLike[str] | None = None,
    build_if_missing: bool = True,
    release: bool = True,
) -> RustRealCacheRuntimeStats:
    if not parts:
        return RustRealCacheRuntimeStats(
            status=0,
            elapsed_ns=0,
            process_jobs_enqueued=0,
            process_jobs_finished=0,
            realtime_process_jobs=0,
            prefetch_process_jobs=0,
            process_priority_steals=0,
            process_worker_ns=0,
            parts=0,
            event_rows=0,
            origins_seen=0,
            samples=0,
            batches=0,
            invalid_origins=0,
            ordinal_mismatches=0,
            event_cache_rebuilds=0,
            event_cache_appends=0,
            event_cache_reused=0,
            bytes_input=0,
            checksum_bits=0,
        )
    config = config or RustRealCacheRuntimeConfig(part_count=len(parts))
    config.part_count = int(len(parts))
    kept_alive: list[tuple[Any, Any, Any, Any]] = []
    ffi_parts = (_FfiRealCachePart * len(parts))()
    import numpy as np

    for index, part in enumerate(parts):
        ordinals = _as_contiguous_numpy(part.ordinals, dtype="<u8")
        features = _as_contiguous_numpy(part.features, dtype="<f4")
        if features.ndim != 2:
            raise ValueError(f"part {index} features must be 2D row-major [events, features]")
        origin_offsets = _as_contiguous_numpy(part.origin_offsets, dtype="<i8")
        origin_ordinals = _as_contiguous_numpy(part.origin_ordinals, dtype="<u8")
        if int(ordinals.shape[0]) != int(features.shape[0]):
            raise ValueError(f"part {index} ordinals/features row mismatch: {ordinals.shape[0]} vs {features.shape[0]}")
        if int(origin_offsets.shape[0]) != int(origin_ordinals.shape[0]):
            raise ValueError(f"part {index} origin offset/ordinal mismatch: {origin_offsets.shape[0]} vs {origin_ordinals.shape[0]}")
        kept_alive.append((ordinals, features, origin_offsets, origin_ordinals))
        ffi_parts[index] = _FfiRealCachePart(
            ticker_id=int(part.ticker_id),
            event_rows=int(ordinals.shape[0]),
            origin_count=int(origin_offsets.shape[0]),
            feature_count=int(features.shape[1]),
            priority=int(part.priority),
            ordinals=ordinals.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
            features=features.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            origin_offsets=origin_offsets.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
            origin_ordinals=origin_ordinals.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
        )
    del np
    ffi_config = _FfiRealCacheConfig(**asdict(config))
    ffi_stats = _FfiRealCacheStats()
    lib = load_rust_library(library_path, build_if_missing=build_if_missing, release=release)
    if not hasattr(lib, "rolling_loader_rust_profile_real_cache"):
        raise RuntimeError("Loaded Rust library does not expose rolling_loader_rust_profile_real_cache; rebuild the DLL.")
    status = int(lib.rolling_loader_rust_profile_real_cache(ctypes.byref(ffi_config), ffi_parts, ctypes.byref(ffi_stats)))
    if status != 0:
        raise RuntimeError(f"rolling_loader_rust_profile_real_cache failed with status={status}")
    _ = kept_alive
    payload = {name: int(getattr(ffi_stats, name)) for name, _ctype in _FfiRealCacheStats._fields_}
    return RustRealCacheRuntimeStats(**payload)


def profile_rust_native_cache(
    config: RustNativeCacheProfileConfig,
    *,
    library_path: str | os.PathLike[str] | None = None,
    build_if_missing: bool = True,
    release: bool = True,
) -> RustNativeCacheProfileStats:
    cache_root = str(Path(config.cache_root)).encode("utf-8")
    month = str(config.month).encode("utf-8")
    ffi_config = _FfiNativeCacheProfileConfig(
        cache_root=ctypes.c_char_p(cache_root),
        month=ctypes.c_char_p(month),
        ticker_limit=max(0, int(config.ticker_limit)),
        batch_size=max(1, int(config.batch_size)),
        max_batches=max(1, int(config.max_batches)),
        event_stream_len=max(1, int(config.event_stream_len)),
        read_workers=max(1, int(config.read_workers)),
        strict=1 if bool(config.strict) else 0,
    )
    ffi_stats = _FfiNativeCacheProfileStats()
    lib = load_rust_library(library_path, build_if_missing=build_if_missing, release=release)
    if not hasattr(lib, "rolling_loader_rust_profile_native_cache"):
        raise RuntimeError("Loaded Rust library does not expose rolling_loader_rust_profile_native_cache; rebuild the DLL.")
    status = int(lib.rolling_loader_rust_profile_native_cache(ctypes.byref(ffi_config), ctypes.byref(ffi_stats)))
    payload = {name: int(getattr(ffi_stats, name)) for name, _ctype in _FfiNativeCacheProfileStats._fields_}
    result = RustNativeCacheProfileStats(**payload)
    if status != 0:
        raise RuntimeError(f"rolling_loader_rust_profile_native_cache failed with status={status}: {result.to_dict()}")
    return result


def assemble_tensors_with_rust(
    values: dict[str, Any],
    config: RustTensorAssemblyConfig | None = None,
    *,
    row_indices: Any | None = None,
    library_path: str | os.PathLike[str] | None = None,
    build_if_missing: bool = True,
    release: bool = True,
    verify: bool = False,
) -> RustTensorAssemblyResult:
    """Assemble a nested numeric tensor tree through the Rust copy/gather runtime.

    `values` may contain nested dictionaries of NumPy arrays. Object arrays and
    metadata fields are copied on the Python side because they are not model
    tensors. Numeric and boolean arrays are passed to Rust as raw contiguous byte
    rows. If `row_indices` is provided, Rust gathers source rows into the output
    rows; otherwise it copies each tensor contiguously.
    """

    import numpy as np

    prepared: list[tuple[str, Any, Any, Any | None]] = []
    outputs: dict[str, Any] = {}
    index_array = None
    if row_indices is not None:
        index_array = _as_contiguous_numpy(row_indices, dtype="<u8")
        if index_array.ndim != 1:
            raise ValueError("row_indices must be a 1D array when provided.")
    _prepare_tensor_tree(values, outputs, prepared, row_indices=index_array)
    if not prepared:
        return RustTensorAssemblyResult(
            stats=RustTensorAssemblyStats(
                status=0,
                elapsed_ns=0,
                jobs_enqueued=0,
                jobs_finished=0,
                realtime_jobs=0,
                prefetch_jobs=0,
                priority_steals=0,
                worker_ns=0,
                tensors=0,
                rows_copied=0,
                bytes_copied=0,
                contiguous_tensors=0,
                gathered_tensors=0,
                invalid_specs=0,
                checksum_bits=0,
            ),
            outputs=outputs,
        )
    cfg = config or RustTensorAssemblyConfig()
    cfg.tensor_count = int(len(prepared))
    ffi_config = _FfiTensorAssemblyConfig(**asdict(cfg))
    ffi_specs = (_FfiTensorAssemblySpec * len(prepared))()
    keep_alive: list[Any] = []
    for index, (_path, source, dest, indices) in enumerate(prepared):
        rows = int(dest.shape[0]) if dest.ndim > 0 else 1
        source_rows = int(source.shape[0]) if source.ndim > 0 else 1
        if source.ndim == 0:
            row_width_bytes = int(source.dtype.itemsize)
        else:
            row_width_bytes = int(source.dtype.itemsize * max(1, int(source.size) // max(1, source_rows)))
        keep_alive.append((source, dest, indices))
        ffi_specs[index] = _FfiTensorAssemblySpec(
            source=ctypes.c_void_p(int(source.ctypes.data)),
            dest=ctypes.c_void_p(int(dest.ctypes.data)),
            row_indices=ctypes.c_void_p(0 if indices is None else int(indices.ctypes.data)),
            rows=int(rows),
            source_rows=int(source_rows),
            row_width_bytes=int(row_width_bytes),
            priority=0,
        )
    lib = load_rust_library(library_path, build_if_missing=build_if_missing, release=release)
    if not hasattr(lib, "rolling_loader_rust_assemble_tensors"):
        raise RuntimeError("Loaded Rust library does not expose rolling_loader_rust_assemble_tensors; rebuild the DLL.")
    ffi_stats = _FfiTensorAssemblyStats()
    status = int(lib.rolling_loader_rust_assemble_tensors(ctypes.byref(ffi_config), ffi_specs, ctypes.byref(ffi_stats)))
    if status != 0:
        raise RuntimeError(f"rolling_loader_rust_assemble_tensors failed with status={status}")
    stats_payload = {name: int(getattr(ffi_stats, name)) for name, _ctype in _FfiTensorAssemblyStats._fields_}
    if verify:
        _verify_tensor_tree(values, outputs, row_indices=index_array)
    _ = keep_alive
    del np
    return RustTensorAssemblyResult(stats=RustTensorAssemblyStats(**stats_payload), outputs=outputs)


def _prepare_tensor_tree(source_tree: dict[str, Any], output_tree: dict[str, Any], prepared: list[tuple[str, Any, Any, Any | None]], *, row_indices: Any | None, prefix: str = "") -> None:
    import numpy as np

    for key, value in source_tree.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            nested: dict[str, Any] = {}
            output_tree[str(key)] = nested
            _prepare_tensor_tree(value, nested, prepared, row_indices=row_indices, prefix=path)
            continue
        if not isinstance(value, np.ndarray) or value.dtype == object:
            output_tree[str(key)] = value.copy() if hasattr(value, "copy") else value
            continue
        source = np.ascontiguousarray(value)
        if source.ndim == 0:
            dest_shape = source.shape
            indices = None
        elif row_indices is not None:
            max_index = int(row_indices.max()) if int(row_indices.shape[0]) else -1
            if max_index >= int(source.shape[0]):
                raise ValueError(f"row_indices references row {max_index} but {path} has only {source.shape[0]} rows.")
            dest_shape = (int(row_indices.shape[0]), *source.shape[1:])
            indices = row_indices
        else:
            dest_shape = source.shape
            indices = None
        dest = np.empty(dest_shape, dtype=source.dtype)
        output_tree[str(key)] = dest
        prepared.append((path, source, dest, indices))


def _verify_tensor_tree(source_tree: dict[str, Any], output_tree: dict[str, Any], *, row_indices: Any | None, prefix: str = "") -> None:
    import numpy as np

    for key, value in source_tree.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        out = output_tree[str(key)]
        if isinstance(value, dict):
            _verify_tensor_tree(value, out, row_indices=row_indices, prefix=path)
            continue
        if not isinstance(value, np.ndarray) or value.dtype == object:
            continue
        expected = value[row_indices] if row_indices is not None and value.ndim > 0 else value
        if not np.array_equal(np.asarray(expected), np.asarray(out)):
            raise RuntimeError(f"Rust tensor assembly verification failed for {path}.")

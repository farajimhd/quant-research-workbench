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
    ticker_count: int = 2_000
    origins_per_ticker: int = 512
    event_stream_len: int = 1_024
    event_feature_count: int = 25
    batch_size: int = 1_024
    realtime_read_workers: int = 16
    prefetch_read_workers: int = 16
    realtime_process_workers: int = 16
    prefetch_process_workers: int = 16
    prefetch_ticker_count: int = 2_000
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

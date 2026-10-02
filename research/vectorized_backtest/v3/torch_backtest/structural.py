"""Exact causal V7 preparation shared by every Torch candidate.

Read prior-session checkpoints at 04:00 ET, advance the canonical streaming
engine on completed 1s bars, and retain the nearest 15 resistance prices per
second. This is a private research cache, never an ARTE producer or a Strategy 1
candidate product. The sequential fitter runs once per ticker, outside replay.
"""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
import polars as pl

from .runtime import file_hash, require_runtime, write_json

VERSION = "squeeze-causal-v7-stream-1"
FIELDS = ("time_us", "open_int_1000", "high_int_1000", "low_int_1000", "close_int_1000", "volume_1000")


@dataclass
class PreparedStructure:
    # Dense targets [time, listing, 15] avoid scanning historical intervals in
    # every GPU step. Validity [time, listing] distinguishes missing input.
    targets: np.ndarray
    valid: np.ndarray
    token: str
    metrics: dict


def algorithm_hash():
    """Seal the exact engine, projection, validation, seed decoder and fitter."""
    root = Path(__file__).resolve().parents[4]
    paths = [Path(__file__), root / "src/backend/fixed_v7_stream.py",
             root / "src/backend/structural_v7_seed.py", root / "src/trading_runtime/strategy_one_v7.py"]
    paths.extend(sorted((root / "src/market_engine").glob("*.py")))
    h = sha256()
    for path in paths:
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def worker_budget(requested=0):
    """Bound CPU preparation separately from GPU candidate sizing."""
    cpus = os.cpu_count() or 2
    if os.name == "nt":
        import ctypes
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]]
        memory = Memory(); memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError("Cannot read CPU preparation memory budget")
        free = memory.available
    else:
        free = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    # Reserve 25% CPU/RAM and budget at least 512 MiB per admitted worker.
    if free * .75 < 512 * 1024**2:
        raise MemoryError("Insufficient RAM for one structural worker")
    maximum = max(1, min(32, max(1, cpus - max(2, cpus//4)), int(free*.75//(512*1024**2))))
    if type(requested) is not int or requested < 0 or requested > maximum:
        raise ValueError(f"Structural workers must be auto/0 or 1..{maximum}")
    return requested or maximum


def _initialize_worker():
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # Each fitter lane uses one numeric thread; ticker parallelism owns the CPU.
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)


def stream_ticker(ticker, day, seed, splits, rows, asks, clocks):
    """Canonical streaming projection, with no future bar or checkpoint access.

    rows [observations, 6] are ordered completed integer OHLC / fractional
    volume. asks [seconds], clocks [seconds] are causal market lanes. Returns
    targets [seconds, 15] and valid [seconds]. Unavailable targets are +inf.
    """
    from src.backend.fixed_v7_stream import FixedV7Stream
    from src.market_engine.derived_trade_policy import POLICY
    session = date.fromisoformat(day)
    # Use the canonical New York 04:00 start from the engine, including DST.
    stream = FixedV7Stream(seed, ticker=ticker, session=session, splits=splits, consume_seed=True)
    start_us = int(stream.engine.start * 1_000_000)
    seed_policy = seed['input_policy'] if seed['levels'] else POLICY
    targets = np.full((len(clocks), 15), np.inf, dtype=np.float64)
    valid = np.zeros(len(clocks), dtype=bool)
    previous = start_us
    cached_geometry = None
    lower = np.empty(0, dtype=np.float64)
    for stamp, op, high, low, close, volume in zip(*(rows[name] for name in FIELDS)):
        stamp = int(stamp)
        if stamp <= previous or (stamp-start_us) % 1_000_000 or stamp > int(clocks[-1])*1_000_000:
            raise ValueError(f"{ticker}: structural bar clock is duplicate, unordered or beyond cutoff")
        previous = stamp
        values = (op, high, low, close, volume)
        if not all(np.isfinite(v) for v in values) or not 0 < low <= min(op, close) <= max(op, close) <= high or volume < 0:
            raise ValueError(f"{ticker}: invalid completed structural OHLC/volume")
        second = (stamp-start_us)//1_000_000
        stream.update_second(dict(resolution_ms=1000, price_valid=1, extremes_valid=1,
            open_int=int(op), high_int=int(high), low_int=int(low), close_int=int(close), volume=float(volume)),
            completed_second_ms=int(second*1000))
        index = int(np.searchsorted(clocks, stamp//1_000_000))
        if index >= len(clocks) or int(clocks[index])*1_000_000 != stamp:
            raise ValueError(f"{ticker}: structural clock is outside the prepared tape")
        valid[index] = True
        # Freshness and confirmed_at are checked at THIS boundary. Projection is
        # memoized by canonical engine revision; observations never replay twice.
        geometry = stream.strategy_one_levels(as_of=datetime.fromtimestamp(stamp/1_000_000, timezone.utc),
                                              seed_policy=seed_policy)
        ask = asks[index]
        if not np.isfinite(ask) or ask <= 0 or not geometry:
            continue
        if geometry is not cached_geometry:
            lower = np.fromiter((float(r['lower']) for r in geometry if r['role']=='resistance'), dtype=np.float64)
            lower.sort()
            cached_geometry = geometry
        offset = np.searchsorted(lower, ask, side='right')
        selected = lower[offset:offset+15]
        targets[index, :len(selected)] = selected
    return targets, valid, dict(input_bars=len(rows['time_us']), consumed_bars=stream.engine.bars_processed,
                               excluded_early_seconds=stream.engine.excluded_early_seconds,
                               seed_session=seed['session'], seed_available_at=seed['available_at'])


def _signature(ticker, day, seed, splits, rows, asks, clocks, source_key, algorithm):
    h = sha256()
    for name in FIELDS:
        value = np.ascontiguousarray(rows[name])
        h.update(name.encode()); h.update(str(value.dtype).encode()); h.update(value.tobytes())
    h.update(np.ascontiguousarray(asks).tobytes()); h.update(np.ascontiguousarray(clocks).tobytes())
    return dict(version=VERSION, ticker=ticker, session=day, seed_hash=seed['checkpoint_hash'],
                source_checkpoint_hash=seed['source_checkpoint_hash'], seed_session=seed['session'],
                seed_available_at=seed['available_at'], splits=splits, source_key=source_key,
                market_hash=h.hexdigest(), algorithm=algorithm,
                numerical_versions={n: version(n) for n in ('numpy', 'scipy')})


def _cache_key(signature):
    return sha256(json.dumps(signature, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@contextmanager
def _claim(path, name="active.lock"):
    # Kernel locks release on normal exit AND process death. Keep the lock file
    # permanently: unlinking it would allow concurrent owners of different inodes.
    handle = os.open(path / name, os.O_CREAT | os.O_RDWR)
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError(f"Structural cache is already owned by another preparation: {path}") from None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError(f"Structural cache is already owned by another preparation: {path}") from None
        locked = True
        os.write(handle, str(os.getpid()).encode())
        yield
    finally:
        if locked:
            os.lseek(handle, 0, os.SEEK_SET)
            if os.name == 'nt':
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def _compute(ticker, day, seed, splits, rows, asks, clocks, path, signature):
    # A parent killed during fitting must not allow a restarted job to race its
    # still-running worker. The worker owns an additional kernel lifetime lock.
    with _claim(Path(path), "worker.lock"):
        if (Path(path) / 'complete.json').exists():
            return _load(Path(path), signature, len(clocks))[2]
        return _compute_locked(ticker, day, seed, splits, rows, asks, clocks, path, signature)


def _compute_locked(ticker, day, seed, splits, rows, asks, clocks, path, signature):
    started = perf_counter()
    targets, valid, metrics = stream_ticker(ticker, day, seed, splits, rows, asks, clocks)
    path = Path(path)
    temporary = path/(uuid4().hex+'.npz')
    np.savez_compressed(temporary, targets=targets, valid=valid)
    destination = path/'arrays.npz'
    temporary.replace(destination)
    receipt = dict(signature=signature, array_hash=file_hash(destination), **metrics,
                   preparation_seconds=perf_counter()-started)
    write_json(path/'complete.json', receipt)
    return receipt


def _load(path, signature, seconds):
    receipt = json.loads((path/'complete.json').read_text())
    if receipt['signature'] != signature or file_hash(path/'arrays.npz') != receipt['array_hash']:
        raise ValueError(f"Structural cache integrity mismatch: {signature['ticker']}")
    with np.load(path/'arrays.npz', allow_pickle=False) as archive:
        targets, valid = archive['targets'], archive['valid']
    if targets.shape != (seconds, 15) or targets.dtype != np.float64 or valid.shape != (seconds,) or valid.dtype != bool:
        raise ValueError("Structural cache shape/dtype mismatch")
    if not np.all((np.isfinite(targets) & (targets>0)) | np.isposinf(targets)) or np.any(targets[:, 1:] < targets[:, :-1]) or np.any(~valid[:, None] & ~np.isposinf(targets)):
        raise ValueError("Structural cache contains invalid resistance prices")
    return targets, valid, receipt


def prepare_structure(reader, market, seeds, bars, tickers, asks, clocks, directory, source_key,
                      *, workers=0, progress=print):
    """Bounded ticker pool with batched seed reads and immutable per-ticker cache.

    Exactly one prior book per ticker is loaded/verified even on cache reuse.
    Completion is durable only after the array hash and receipt have been read
    back. Progress counts ticker preparations; it never counts grid candidates.
    """
    from src.backend.structural_v7_seed import load_seeds_batch, split_evidence_batch
    day = market.sessions[0]; session = date.fromisoformat(day)
    coverage = {r['ticker']: r for r in seeds.units if r['backtest_session']==day}
    if market.sessions != (day,) or set(coverage) != set(tickers) or seeds.build_id != market.build_id:
        raise ValueError("Structural seeds do not bind the selected session/tickers/build")
    if asks.shape != (len(clocks), len(tickers)) or not len(clocks):
        raise ValueError("Structural asks require [seconds, listing] lanes")
    width = min(worker_budget(workers), len(tickers))
    directory = require_runtime(directory/'structural-stream-cache')
    algorithm = algorithm_hash()
    targets = np.full((len(clocks), len(tickers), 15), np.inf, dtype=np.float64)
    valid = np.zeros((len(clocks), len(tickers)), dtype=bool)
    selected = bars.filter((pl.col('price_valid_1000')==1) & (pl.col('extremes_valid_1000')==1))
    groups = selected.select('ticker', *FIELDS).sort('ticker', 'time_us').partition_by('ticker', as_dict=True)
    pending = {}; receipts = {}; completed = reused = 0; started = perf_counter()

    def emit(message):
        progress(dict(stage='Stream structural levels', completed=completed, total=len(tickers), unit='tickers',
                      message=f"{message}; {width} CPU workers, {len(pending)} pending ticker jobs, {reused} cache hits"))

    def collect(futures):
        nonlocal completed
        for future in futures:
            index, path, signature, claim = pending.pop(future)
            try:
                future.result()
                target, clock, receipt = _load(path, signature, len(clocks))
                targets[:, index], valid[:, index] = target, clock
                receipts[tickers[index]] = receipt
                completed += 1
            finally:
                claim.__exit__(None, None, None)
        if futures:
            emit('Streaming result saved')

    # Control numeric oversubscription before Windows spawns fresh interpreters.
    prior_env = {name: os.environ.get(name) for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')}
    os.environ.update({name:'1' for name in prior_env})
    emit('Loading session-start checkpoints and causal split evidence')
    try:
        with ProcessPoolExecutor(max_workers=width, initializer=_initialize_worker) as pool:
            for offset in range(0, len(tickers), 8):
                batch = tuple(tickers[offset:offset+8])
                # Batched bounded database reads never use end-of-day checkpoints.
                books = load_seeds_batch(reader, tickers=batch, session=session,
                                         coverage={t: coverage[t] for t in batch})
                splits = split_evidence_batch(reader, seed_sessions={t: date.fromisoformat(books[t]['session']) for t in batch}, session=session)
                for index, ticker in enumerate(batch, start=offset):
                    frame = groups.get((ticker,), pl.DataFrame(schema={'ticker':pl.String, **{f:pl.Float64 for f in FIELDS}}))
                    rows = {name: np.ascontiguousarray(frame[name].to_numpy()) for name in FIELDS}
                    ask = np.ascontiguousarray(asks[:, index])
                    signature = _signature(ticker, day, books[ticker], splits[ticker], rows, ask, clocks, source_key, algorithm)
                    path = require_runtime(directory/_cache_key(signature))
                    if (path/'complete.json').exists():
                        target, clock, receipt = _load(path, signature, len(clocks))
                        targets[:, index], valid[:, index] = target, clock
                        receipts[ticker] = receipt
                        completed += 1; reused += 1
                        emit('Verified cached causal structure')
                        continue
                    if len(pending) >= 2*width:
                        collect(wait(pending, return_when=FIRST_COMPLETED).done)
                    claim = _claim(path); claim.__enter__()
                    try:
                        future = pool.submit(_compute, ticker, day, books[ticker], splits[ticker], rows, ask, clocks, path, signature)
                    except BaseException:
                        claim.__exit__(None, None, None)
                        raise
                    pending[future] = (index, path, signature, claim)
                if pending:
                    collect([f for f in pending if f.done()])
            while pending:
                collect(wait(pending, return_when=FIRST_COMPLETED).done)
    finally:
        for future, (_, _, _, claim) in pending.items():
            future.cancel(); claim.__exit__(None, None, None)
        for name, value in prior_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    token = _cache_key({t: receipts[t]['signature'] for t in tickers})
    return PreparedStructure(targets, valid, token, dict(tickers=len(tickers), reused=reused, workers=width,
        preparation_seconds=perf_counter()-started, algorithm=algorithm,
        source_checkpoints={t: receipts[t]['signature']['source_checkpoint_hash'] for t in tickers}))

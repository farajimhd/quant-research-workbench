"""Bounded Arrow producer / Parquet writer overlap with durable file seals."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from pathlib import Path
from time import perf_counter
import os
import uuid
import numpy as np
import polars as pl
import pyarrow.parquet as pq
import pyarrow as pa
from urllib import request, error
from .contract import sha, digest, write_json
import json


def batches(c,statement):
    """Expose the schema even when a certified sparse product has zero rows."""
    params=dict(c.default_query_params,query_id='shards-'+uuid.uuid4().hex)
    req=request.Request(c._request_url(params),data=(statement+' FORMAT ArrowStream').encode(),method='POST')
    if c.user:req.add_header('X-ClickHouse-User',c.user)
    if c.password:req.add_header('X-ClickHouse-Key',c.password)
    try:response=request.urlopen(req,timeout=c.timeout_seconds)
    except error.HTTPError as exc:
        message=exc.read().decode('utf-8',errors='replace')
        if c.password:message=message.replace(c.password,'[redacted]')
        raise RuntimeError('ClickHouse '+str(exc.code)+': '+message) from None
    with response:
        with pa.ipc.open_stream(response) as reader:
            seen=False
            for batch in reader:
                seen=True;yield batch
            if not seen:
                yield pa.RecordBatch.from_arrays([pa.array([],type=f.type) for f in reader.schema],schema=reader.schema)


def validate(frame, keys, previous=None):
    if not frame.height:
        return previous
    if frame.select(pl.any_horizontal(pl.col(keys).is_null()).any()).item():
        raise ValueError('Null market identity/clock')
    if not frame.select(keys).equals(frame.select(keys).sort(keys)) or frame.select(pl.struct(keys).is_duplicated().any()).item():
        raise ValueError('Duplicate or unordered market keys')
    first, last = tuple(frame.select(keys).row(0)), tuple(frame.select(keys).row(-1))
    if previous is not None and first <= previous:
        raise ValueError('Cross-batch market ordering/duplicate error')
    for name, dtype in frame.schema.items():
        if dtype in (pl.Float32, pl.Float64) and not np.isfinite(frame[name].to_numpy()).all():
            raise ValueError('Nonfinite market values: '+name)
    if 'quote_us' in frame and frame.filter((pl.col('quote_us') > pl.col('time_us')) | (pl.col('capacity') < 0)).height:
        raise ValueError('Execution availability/capacity violation')
    if 'available_at_us' in frame and frame.filter(pl.col('available_at_us') < pl.col('time_us')).height:
        raise ValueError('Daily availability precedes close')
    return last


def stream(c, statement, path, keys, progress=lambda **kw: None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    seal = path.with_suffix('.json'); identity = digest(dict(sql=statement, keys=keys))
    if seal.exists():
        saved = json.loads(seal.read_text())
        if saved['identity'] != identity or sha(path) != saved['sha256']:
            raise ValueError('Immutable shard/query changed: '+str(path))
        return dict(saved, reused=True)
    partial = path.with_suffix('.partial')
    began = perf_counter(); rows = byte_count = 0
    previous = None; writer = None; pending = deque(); write_seconds = 0.
    def append(batch):
        nonlocal writer, previous, write_seconds
        tick = perf_counter()
        frame = pl.from_arrow(batch)
        previous = validate(frame, keys, previous)
        if writer is None:
            writer = pq.ParquetWriter(partial, batch.schema, compression='zstd', compression_level=1)
        writer.write_batch(batch)
        write_seconds += perf_counter()-tick
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='shard-disk') as disk:
            for batch in batches(c,statement):
                if len(pending) >= 2:
                    pending.popleft().result()
                pending.append(disk.submit(append, batch))
                rows += batch.num_rows; byte_count += batch.nbytes
                progress(rows=rows, bytes=byte_count)
            while pending:
                pending.popleft().result()
        if writer is None:
            raise ValueError('Arrow stream omitted schema/empty batch')
        writer.close(); writer = None
        os.replace(partial, path)
        saved = dict(identity=identity, sha256=sha(path), rows=rows, arrow_bytes=byte_count,
            disk_bytes=path.stat().st_size, seconds=perf_counter()-began, writer_seconds=write_seconds)
        write_json(seal, saved)
        return dict(saved, reused=False)
    finally:
        if writer is not None:
            writer.close()

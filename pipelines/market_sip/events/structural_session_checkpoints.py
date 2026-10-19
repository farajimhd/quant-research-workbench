"""V7 streaming boundary states seeded by immutable retrospective AH books.

An existing AH book is never replaced by a streaming result. PM and REGULAR
are lossless same-day resume points, not historical next-day seed books.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import prod

from pipelines.certified_research.checkpoints import boundaries
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.streaming_level_book import StreamingLevelBook
from research.level_book.v7.campaign import fit_day
from research.level_book.v7.clickhouse_persistence import compact_checkpoints

VERSION = 'structural-session-checkpoints-v1'
AH = ''  # Existing rows retain their implicit String default and original keys.


def restore_engine(state):
    engine = StreamingLevelBook.restore(state)
    # JSON arrays decode as lists; the kernel's turning extremes are tuples.
    for name in ('high', 'low'):
        point = getattr(engine, name)
        if point is not None:
            setattr(engine, name, tuple(point))
    return engine


def source_rows(client, unit):
    """Read only the pinned completed 1s attempt; no consumer-CLI imports."""
    from .market_day_sql import literal
    query = ('SELECT bucket_index,open_int,high_int,low_int,close_int,volume,price_valid '
        f'FROM arte.bars_v1 WHERE build_id={literal(unit.build_id)} '
        f'AND session_date=toDate({literal(unit.session_date)}) AND ticker={literal(unit.ticker)} '
        f'AND attempt_id=toUUID({literal(unit.attempt_id)}) '
        'AND resolution_ms=1000 AND price_valid=1 AND bucket_index>=14700 '
        'AND bucket_index<72000 ORDER BY bucket_index SETTINGS max_block_size=8192 FORMAT ArrowStream')
    from contextlib import closing
    from uuid import uuid4
    from time import perf_counter
    query_id='market-day-'+uuid4().hex
    client.active=query_id;started=perf_counter();last=14699;count=0
    try:
        with closing(client.http.iter_arrow_record_batches(query,query_id=query_id)) as batches:
            for batch in batches:
                # Arrow avoids JSON text and retains bounded transfer blocks.
                for row in batch.to_pylist():
                    index=int(row['bucket_index']);count+=1
                    if index<=last or index>=72000 or count>57300:
                        raise ValueError('Duplicate or invalid certified 1s bar coordinates')
                    last=index
                    yield row
    except BaseException:
        client.cancel()
        raise
    finally:
        client.active=None
        elapsed=perf_counter()-started
        total=client.profile_totals.setdefault('structural_session_input',dict(count=0,seconds=0.,max_seconds=0.))
        total['count']+=1;total['seconds']+=elapsed;total['max_seconds']=max(total['max_seconds'],elapsed)


def input_bars(day, rows):
    """Convert streamed certified rows in bounded vectorized blocks."""
    from itertools import islice
    import numpy as np
    from .causal_v7_product import bar_cutoff
    # After 04:05 ET the UTC offset is fixed even on DST transition dates.
    base=bar_cutoff(day,14700)-14701
    result=[];rows=iter(rows)
    while block:=list(islice(rows,8192)):
        indices=np.asarray([int(r['bucket_index']) for r in block],dtype=np.int64)
        integers=np.asarray([[int(r[k]) for k in ('open_int','high_int','low_int','close_int')]
                             for r in block],dtype=np.int64)
        prices=integers.astype(np.float64)/10000
        if np.any(integers>2**53) or np.any(integers<-(2**53)):
            prices=np.asarray([[int(r[k])/10000 for k in ('open_int','high_int','low_int','close_int')] for r in block])
        volume=np.asarray([float(r['volume']) for r in block],dtype=np.float64)
        valid=np.asarray([int(r.get('price_valid') or 0) for r in block])
        o,h,l,c=prices.T
        if (np.any(valid!=1) or np.any(indices<14700) or np.any(indices>=72000)
                or np.any(l<=0) or np.any(l>np.minimum(o,c))
                or np.any(h<np.maximum(o,c)) or np.any(volume<0)):
            raise ValueError('V7 input has invalid certified bar coordinates/geometry')
        for t,price,v in zip((indices+base+1).tolist(),prices.tolist(),volume.tolist()):
            result.append(dict(t=float(t),open=price[0],high=price[1],low=price[2],close=price[3],volume=v))
    return result


def calculate(*, prior, ticker, day, bars, actions, regular_close,
              existing_ah=False, persist_intraday=True, resume=None, historical_bars=None):
    """One chronological pass; no future bars configure the streaming engine.

    New AH is calculated separately with the unchanged retrospective fitter.
    PM/REGULAR capture the exact default streaming kernel, including pending
    contacts, noise heaps and discovery state. Boundary timestamps include a
    completed second ending exactly at the cut.
    """
    cuts = boundaries(date.fromisoformat(day), regular_close)
    from src.market_engine.v7_qmd import session_bounds
    begin, end = session_bounds(day)
    if prior is None:
        # Use the existing genesis contract; no fitted future-day parameters.
        from src.market_engine.streaming_level_book import EXTRACTION_VERSION
        from src.market_engine.reaction_band import CONFIG
        prior = dict(ticker=ticker, session='0001-01-01', available_at=begin.timestamp(),
                     levels=[], source_extraction_version=EXTRACTION_VERSION,
                     band_config={**CONFIG, 'coverage': .8})
        prior['checkpoint_hash'] = digest(prior)
    factor = prod(float(row['split_from']) / float(row['split_to']) for row in actions)
    engine = (restore_engine(resume['streaming_state']) if resume else
              StreamingLevelBook(prior, ticker=ticker, session=day,
                  start=begin.timestamp(), end=end.timestamp(),
                  split_factor=factor, split_evidence=actions))
    if engine.ticker != ticker or engine.session != day or engine.prior_hash != prior['checkpoint_hash']:
        raise ValueError('Streaming resume differs from the pinned previous AH')
    last = begin.timestamp()
    for bar in bars:
        if not last < bar['t'] <= end.timestamp():
            raise ValueError('Canonical bars must be unique, ordered and inside session')
        last = bar['t']
    results = {}
    offset = 0
    for boundary in ('PM', 'REGULAR') if persist_intraday else ():
        cut = cuts[boundary].timestamp()
        if resume and cut <= resume['boundary_at']:
            continue
        first=offset
        while offset < len(bars) and bars[offset]['t'] <= cut:offset+=1
        engine.update_many([bar for bar in bars[first:offset] if bar['t']>engine.as_of])
        prefix = bars[:offset]
        state = engine.checkpoint()
        results[boundary] = dict(version=VERSION, ticker=ticker, day=day,
            boundary=boundary, boundary_at=cut, prior_hash=prior['checkpoint_hash'],
            input_hash=digest(prefix), streaming_state=state)
    if not existing_ah:
        # Retain exactly the historical algorithm, never promote a causal PM
        # state into a retrospective daily seed.
        history = bars if historical_bars is None else historical_bars
        if not history:
            raise ValueError('Missing canonical price bars; cannot synthesize an AH book')
        historical = fit_day(prior, ticker, day, history, actions)
        for bar in history:
            historical.update(bar)
        results['AH'] = historical.historical_checkpoint(digest(history))
    return results


def normalized_boundary(value, source_plan_hash):
    """Typed level/observation snapshots plus lossless restore payload.

    Separate boundary keys protect existing AH intervals. Snapshot validity is
    limited to this trading day; intraday rows cannot become next-day seeds.
    """
    import json
    from hashlib import sha256
    # Read-only projection: restore still checks the full state hash/version,
    # then historical_checkpoint makes the independent copy used below. Never
    # update this borrowed engine or mutate its nested checkpoint values.
    engine = StreamingLevelBook.restore(value['streaming_state'],copy_state=False)
    book = engine.historical_checkpoint(value['input_hash'],copy_state=False,available_at=value['boundary_at'])
    levels, observations, coverage = compact_checkpoints([book], source_plan_hash)
    boundary = value['boundary']
    from research.level_book.v7.clickhouse_persistence import datetime64_ns, epoch_ns
    end = datetime64_ns(epoch_ns(engine.end))
    for row in levels:
        row.update(checkpoint_boundary=boundary, retrospective=0, valid_to=end)
    for row in observations:
        row.update(checkpoint_boundary=boundary, valid_to=end)
    certificate = coverage[0]
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(payload.encode()) > 64 * 1024**2:
        raise ValueError('Boundary full-state payload exceeds 64 MiB')
    certificate.update(checkpoint_boundary=boundary, streaming_state=payload,
        streaming_state_hash=sha256(payload.encode()).hexdigest(),
        published_at=datetime64_ns(epoch_ns(datetime.now(timezone.utc).timestamp())))
    return levels, observations, certificate


def restore_boundary(row):
    import json
    value = json.loads(row['streaming_state'])
    if digest(value) != row['streaming_state_hash'] or value['version'] != VERSION:
        raise ValueError('Streaming boundary payload integrity/version mismatch')
    engine = restore_engine(value['streaming_state'])
    if (value['ticker'] != row['ticker'] or value['day'] != str(row['session_date'])
            or value['boundary'] != row['checkpoint_boundary']
            or engine.as_of > value['boundary_at']):
        raise ValueError('Streaming boundary header differs from payload')
    return engine, value

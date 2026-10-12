"""One causal kernel for new session checkpoints and historical checkpoint fill."""
from __future__ import annotations

from datetime import date,datetime,time,timezone
from hashlib import sha256
from uuid import UUID
import msgpack

from src.market_engine.streaming_level_book import StreamingLevelBook
from src.market_engine.historical_level_checkpoint import digest as book_digest
from .checkpoints import CheckpointBinding,boundaries,encode,restore,NY
from .contracts import literal,identifier,TRADE_POLICY


def next_day_engine(previous: StreamingLevelBook,day: date,*,previous_checkpoint,
                    split_factor=1.,split_evidence=()):
    """Day rollover once; same-day restoration must use restore instead."""
    if previous.session>=day.isoformat() or previous.seed_input_policy!=previous.input_policy:
        raise ValueError('Prior V7 state is incompatible or contains legacy seed lineage')
    if previous_checkpoint['boundary']!='AH' or previous_checkpoint['boundary_us']!=round(previous.end*1_000_000):
        raise ValueError('Day rollover requires the certified prior AH boundary')
    restored=restore(previous_checkpoint['payload'],previous_checkpoint['state_hash'],previous_checkpoint['binding'])
    if restored.checkpoint()!=previous.checkpoint():raise ValueError('Prior engine differs from its AH checkpoint')
    seed=previous.historical_checkpoint(previous.checkpoint()['hash'])
    # This conversion was produced from causal full state, not retrospective
    # historical discovery. Keep that distinction in the new seed hash.
    seed['retrospective']=False
    seed['checkpoint_hash']=book_digest({k:v for k,v in seed.items() if k!='checkpoint_hash'})
    start=datetime.combine(day,time(4),NY).timestamp()
    end=datetime.combine(day,time(20),NY).timestamp()
    return StreamingLevelBook(seed,ticker=previous.ticker,session=day.isoformat(),start=start,end=end,
                              split_factor=split_factor,split_evidence=split_evidence)


def generate(engine: StreamingLevelBook,rows,binding: CheckpointBinding,*,regular_close=None,restore_each=False):
    """Rows are ordered completed valid 1s bars; retain no future fitting input."""
    cuts=boundaries(binding.session,regular_close)
    pending=list(cuts.items());outputs=[];prefix=sha256(bytes.fromhex(binding.input_prefix_hash))
    parent=binding.predecessor_hash
    def save(name,cut):
        nonlocal engine,parent
        bound=CheckpointBinding(binding.dataset_hash,prefix.hexdigest(),parent,binding.ticker,binding.session)
        payload,hash=encode(engine,bound,cut)
        outputs.append(dict(boundary=name,boundary_us=round(cut.timestamp()*1_000_000),
                            last_input_us=round(engine.as_of*1_000_000),parent_hash=parent,
                            state_hash=hash,codec_version=1,payload=payload,binding=bound))
        if restore_each:engine=restore(payload,hash,bound)
        parent=hash
    last=engine.as_of
    for row in rows:
        if row['t']<=last or row['t']>engine.end:
            raise ValueError('V7 input is unordered, repeated or beyond the certified session')
        while pending and row['t']>pending[0][1].timestamp():
            name,cut=pending.pop(0);save(name,cut)
        # The digest advances only when this bar becomes available. Later
        # bars never enter an earlier checkpoint's parameter/hash context.
        prefix.update(msgpack.packb(row,use_bin_type=True))
        engine.update(row,observed_at=row['t']);last=row['t']
    for name,cut in pending:save(name,cut)
    return tuple(outputs),engine


def bars_sql(dataset_id,attempt_id,listing_key,day,database='arte'):
    identifier(database);UUID(dataset_id);UUID(attempt_id)
    if type(listing_key) is not int or not 0<listing_key<2**64:raise ValueError('Invalid listing key')
    origin=round(datetime.combine(day,time(),NY).timestamp()*1_000_000)
    return (f'SELECT toUInt64({origin})+(toUInt64(bucket_index)+1)*1000000 close_us,'
            'open_int,high_int,low_int,close_int,volume '
            f'FROM {database}.research_bars_v1 WHERE dataset_id=toUUID({literal(dataset_id)}) '
            f'AND attempt_id=toUUID({literal(attempt_id)}) AND listing_key={listing_key} '
            f'AND session_date=toDate({literal(day)}) AND resolution_ms=1000 '
            'AND price_valid=1 AND extremes_valid=1 ORDER BY bucket_index')


def decode_bars(rows):
    return tuple(dict(t=int(r['close_us'])//1_000_000,open=int(r['open_int'])/10000.,
                      high=int(r['high_int'])/10000.,low=int(r['low_int'])/10000.,
                      close=int(r['close_int'])/10000.,volume=float(r['volume'])) for r in rows)


def projection_sql(outputs,dataset_id,attempt_id,listing_key,day,*,include_payload=True,regular_close=None):
    from .schema import TABLES
    names=next(t for t in TABLES if t.name=='research_v7_checkpoint_v1').columns
    UUID(dataset_id);UUID(attempt_id)
    if type(listing_key) is not int or not 0<listing_key<2**64:raise ValueError('Invalid listing key')
    if tuple(o['boundary'] for o in outputs)!=('PM','REGULAR','AH'):
        raise ValueError('V7 publication must contain exactly all three session boundaries')
    cuts=boundaries(day,regular_close)
    pieces=[];previous=None
    for output in outputs:
        bound=output['binding']
        decoded=restore(output['payload'],output['state_hash'],bound,expected_boundary_us=output['boundary_us'])
        if (bound.session!=day or output['parent_hash']!=bound.predecessor_hash
                or output['boundary_us']!=round(cuts[output['boundary']].timestamp()*1_000_000)
                or output['last_input_us']!=round(decoded.as_of*1_000_000)
                or type(output['codec_version']) is not int or output['codec_version']!=1
                or bound.dataset_hash!=outputs[0]['binding'].dataset_hash
                or bound.ticker!=outputs[0]['binding'].ticker
                or (previous is not None and output['parent_hash']!=previous)):
            raise ValueError('Checkpoint header/source/predecessor chain mismatch')
        previous=output['state_hash']
        boundary_code={'PM':1,'REGULAR':2,'AH':3}[output['boundary']]
        fields=(f'toUUID({literal(dataset_id)})',f'toDate({literal(day)})',
            f'toUInt64({listing_key})',f'toUUID({literal(attempt_id)})',f'toUInt8({boundary_code})',
            f"toUInt64({output['boundary_us']})",f"toUInt64({output['last_input_us']})",
            literal(output['parent_hash']),literal(output['state_hash']),f"toUInt8({output['codec_version']})",
            "unhex("+literal(output['payload'].hex())+")" if include_payload else "''")
        pieces.append('SELECT '+','.join(f'{value} AS {column.name}' for column,value in zip(names,fields)))
    return ' UNION ALL '.join(pieces)


def insert_sql(outputs,dataset_id,attempt_id,listing_key,day,database='arte',*,maximum_bytes=64*1024**2,regular_close=None):
    identifier(database);UUID(dataset_id);UUID(attempt_id)
    sql=f'INSERT INTO {database}.research_v7_checkpoint_v1 '+projection_sql(outputs,dataset_id,attempt_id,listing_key,day,
                                                                         regular_close=regular_close)
    if len(sql.encode())>maximum_bytes:raise ValueError('Checkpoint transport exceeds the explicit bound')
    return sql


def expected_evidence_sql(outputs,dataset_id,attempt_id,listing_key,day,*,regular_close=None):
    from .schema import TABLES
    from .publication import evidence_sql
    table=next(t for t in TABLES if t.name=='research_v7_checkpoint_v1')
    return evidence_sql(table,projection_sql(outputs,dataset_id,attempt_id,listing_key,day,
                        include_payload=False,regular_close=regular_close),'tuple(boundary)')

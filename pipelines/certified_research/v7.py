"""One causal kernel for new session checkpoints and historical checkpoint fill."""
from __future__ import annotations

from datetime import date,datetime,time,timezone,timedelta
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID
import msgpack

from src.market_engine.streaming_level_book import StreamingLevelBook
from src.market_engine.historical_level_checkpoint import digest as book_digest
from .checkpoints import CheckpointBinding,boundaries,encode,restore,NY
from .contracts import literal,identifier,TRADE_POLICY,digest,REPORTING_REVISION


@dataclass(frozen=True)
class StartAuthority:
    """A private engine plus the verified predecessor used to construct it."""
    engine: StreamingLevelBook
    predecessor_hash: str
    initial_state_hash: str
    adjustment_hash: str=''

    def check(self,expected,*,adjustment_hash=''):
        if (self.predecessor_hash!=expected or self.engine.checkpoint(copy_state=False)['hash']!=self.initial_state_hash):
            raise ValueError('V7 start state differs from its certified predecessor or was mutated')
        if adjustment_hash and self.adjustment_hash!=adjustment_hash:
            raise ValueError('V7 start state differs from its certified corporate adjustment')
        return self.engine

    @classmethod
    def rollover(cls,checkpoint,day,*,split_factor=1.,split_evidence=()):
        previous=restore(checkpoint['payload'],checkpoint['state_hash'],checkpoint['binding'],
                         expected_boundary_us=checkpoint['boundary_us'])
        engine=next_day_engine(previous,day,previous_checkpoint=checkpoint,
            split_factor=split_factor,split_evidence=split_evidence)
        return cls(engine,checkpoint['state_hash'],engine.checkpoint(copy_state=False)['hash'])

    @classmethod
    def genesis(cls,engine,certificate_hash):
        # The launcher must independently certify the explicit context origin.
        CheckpointBinding(certificate_hash,'0'*64,'0'*64,engine.ticker,date.fromisoformat(engine.session))
        if engine.rows or engine.pending or engine.bars_processed or engine.as_of!=engine.start:
            raise ValueError('Genesis authority requires an untouched empty engine')
        return cls(engine,certificate_hash,engine.checkpoint(copy_state=False)['hash'])


def input_origin_hash(recipe_hash,ticker,listing_key,day):
    """Prefix origin contains no future session rows or full-day bar digest."""
    CheckpointBinding(recipe_hash,'0'*64,'0'*64,ticker,day)
    if type(listing_key) is not int or not 0<listing_key<2**64:raise ValueError('Invalid V7 listing identity')
    return digest(dict(version='causal-v7-input-origin-v1',recipe=recipe_hash,ticker=ticker,
        listing=listing_key,session=day,trade_policy=TRADE_POLICY,reporting_revision=REPORTING_REVISION))


def next_day_engine(previous: StreamingLevelBook,day: date,*,previous_checkpoint,
                    split_factor=1.,split_evidence=()):
    """Day rollover once; same-day restoration must use restore instead."""
    if previous.session>=day.isoformat() or previous.seed_input_policy!=previous.input_policy:
        raise ValueError('Prior V7 state is incompatible or contains legacy seed lineage')
    from .source import exchange_sessions
    if exchange_sessions(date.fromisoformat(previous.session)+timedelta(days=1),day)!=(day,):
        raise ValueError('V7 rollover cannot skip required exchange sessions; replay the context gap')
    if previous_checkpoint['boundary']!='AH' or previous_checkpoint['boundary_us']!=round(previous.end*1_000_000):
        raise ValueError('Day rollover requires the certified prior AH boundary')
    restored=restore(previous_checkpoint['payload'],previous_checkpoint['state_hash'],previous_checkpoint['binding'],
                     expected_boundary_us=previous_checkpoint['boundary_us'])
    if restored.checkpoint()!=previous.checkpoint():raise ValueError('Prior engine differs from its AH checkpoint')
    seed=previous.historical_checkpoint(previous.checkpoint()['hash'])
    # This conversion was produced from causal full state, not retrospective
    # historical discovery. Keep that distinction in the new seed hash.
    seed['retrospective']=False
    seed['checkpoint_hash']=book_digest({k:v for k,v in seed.items() if k!='checkpoint_hash'})
    start=datetime.combine(day,time(4),NY).timestamp()
    end=datetime.combine(day,time(20),NY).timestamp()
    from src.market_engine.historical_session_levels import Settings
    return StreamingLevelBook(seed,ticker=previous.ticker,session=day.isoformat(),start=start,end=end,
                              split_factor=split_factor,split_evidence=split_evidence,
                              settings=Settings(**previous.settings),discovery_prominence=previous.discovery_prominence,
                              coverage=previous.coverage)


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


def load_from_bars(query,database,claim,scope,bar_scope,recipe_hash,predecessor_hash,ticker,boundary,*,regular_close=None):
    """Derive expected prefix/chain from certified bars, then restore authority."""
    from .publication import Publisher
    if (bar_scope.product!='bars' or bar_scope.dataset_id!=scope.dataset_id
            or bar_scope.listing_key!=scope.listing_key or bar_scope.session_date!=scope.session_date):
        raise ValueError('Checkpoint bar authority differs in date/identity')
    cuts=boundaries(scope.session_date,regular_close)
    if boundary not in cuts:raise ValueError('Invalid checkpoint boundary')
    publisher=Publisher(query,database,claim);bars=publisher.receipt(bar_scope)
    if bars is None:raise ValueError('Checkpoint source bars are not certified')
    cut=round(cuts[boundary].timestamp()*1_000_000)
    sql=bars_sql(scope.dataset_id,bars['attempt_id'],scope.listing_key,scope.session_date,database)
    rows=query(f'SELECT * FROM ({sql}) WHERE close_us<={cut} ORDER BY close_us LIMIT 57601')
    if len(rows)>57600:raise ValueError('Checkpoint source exceeds bounded 1s session input')
    decoded=iter(decode_bars(rows));current=next(decoded,None)
    prefix=sha256(bytes.fromhex(input_origin_hash(recipe_hash,ticker,scope.listing_key,scope.session_date)))
    parent=predecessor_hash;result=None;previous_time=None
    for name,close in cuts.items():
        while current is not None and current['t']<=close.timestamp():
            if previous_time is not None and current['t']<=previous_time:raise ValueError('Checkpoint bars are repeated or unordered')
            prefix.update(msgpack.packb(current,use_bin_type=True));previous_time=current['t'];current=next(decoded,None)
        binding=CheckpointBinding(recipe_hash,prefix.hexdigest(),parent,ticker,scope.session_date)
        result=load_checkpoint(query,database,claim,scope,binding,name,regular_close=regular_close)
        parent=result[0]['state_hash']
        if name==boundary:break
    if publisher.receipt(bar_scope)!=bars:raise ValueError('Checkpoint bar authority changed during restore')
    return result


def load_checkpoint(query,database,claim,scope,binding,boundary,*,regular_close=None):
    """Restore a certified PM/regular/AH authority without files or day rollover.

    The caller resolves the exact expected input-prefix/predecessor binding.
    Reading an envelope is not permission to trust its self-declared lineage.
    """
    from .publication import Publisher
    from .checkpoints import MAX_PAYLOAD_BYTES
    if scope.product!='v7' or scope.session_date!=binding.session or boundary not in ('PM','REGULAR','AH'):
        raise ValueError('Checkpoint request scope/boundary differs')
    publisher=Publisher(query,database,claim);receipt=publisher.receipt(scope)
    if receipt is None:raise ValueError('V7 checkpoint authority is not certified')
    code={'PM':1,'REGULAR':2,'AH':3}[boundary]
    where=scope.where(receipt['attempt_id'])+f' AND boundary={code}'
    headers=query(f'SELECT boundary_us,last_input_us,parent_hash,state_hash,codec_version,length(payload) bytes '
        f'FROM {identifier(database)}.research_v7_checkpoint_v1 WHERE {where}')
    if len(headers)!=1:raise ValueError('Checkpoint boundary is missing or duplicated')
    row=headers[0];cut=round(boundaries(binding.session,regular_close)[boundary].timestamp()*1_000_000)
    if (int(row['codec_version'])!=1 or int(row['boundary_us'])!=cut
            or row['parent_hash']!=binding.predecessor_hash or not 0<int(row['bytes'])<=MAX_PAYLOAD_BYTES):
        raise ValueError('Checkpoint header is incompatible or exceeds transport bounds')
    payloads=query(f'SELECT hex(payload) payload FROM {database}.research_v7_checkpoint_v1 WHERE {where}')
    if len(payloads)!=1:raise ValueError('Checkpoint payload is missing or duplicated')
    payload=bytes.fromhex(payloads[0]['payload'])
    engine=restore(payload,row['state_hash'],binding,expected_boundary_us=cut)
    if round(engine.as_of*1_000_000)!=int(row['last_input_us']):raise ValueError('Checkpoint input timestamp differs from header')
    if publisher.receipt(scope)!=receipt:raise ValueError('Checkpoint authority changed during restore')
    return dict(boundary=boundary,boundary_us=cut,last_input_us=int(row['last_input_us']),
        parent_hash=row['parent_hash'],state_hash=row['state_hash'],codec_version=1,payload=payload,binding=binding),engine


def authority(request,bars,query,database,claim):
    """Attach independently resolvable context to a verified V7 unit receipt."""
    from .publication import Publisher,Scope
    base=request.unit
    scope=Scope(base.dataset_id,base.day,base.listing_key,'v7',request.parent('v7',(bars['output_hash'],)))
    receipt=Publisher(query,database,claim).receipt(scope)
    if receipt is None:return None
    return dict(receipt,state_context=dict(recipe_hash=request.dataset_hash,source_hash=request.source_hash,
        predecessor_hash=request.prior_v7_hash,ticker=base.ticker,regular_close=request.regular_close.isoformat(),
        bars_parent_hash=request.parent('bars'),adjustment_hash=request.adjustment_hash))


def verify_seed(query,database,claim,parent,recipe_hash,source_hash,rules):
    """Verify an explicit causal seed locator against independent frozen inputs.

    Locator context is untrusted until its bars and V7 receipt lineage match,
    and the AH payload has passed certified-bar prefix/chain reconstruction.
    Retrospective daily books are not accepted by this full-state reader.
    """
    from .publication import Publisher,Scope
    from .producer import MarketRequest
    from .market import MarketUnit
    from .price_inputs import calendar_close
    identifier(database)
    if parent.get('product')!='v7':raise ValueError('Seed must reference causal V7 authority')
    day=date.fromisoformat(str(parent['session_date']));context=parent.get('state_context',{})
    required={'recipe_hash','source_hash','predecessor_hash','ticker','regular_close','bars_parent_hash','adjustment_hash'}
    if set(context)!=required or context['recipe_hash']!=recipe_hash or context['source_hash']!=source_hash:
        raise ValueError('V7 seed recipe/source context differs from frozen authority')
    close=calendar_close(day)
    if datetime.fromisoformat(context['regular_close'])!=close:raise ValueError('V7 seed calendar boundary differs')
    if not context['adjustment_hash'] and not database.startswith('certified_research_test_'):
        raise ValueError('V7 seed requires certified corporate adjustment lineage')
    unit=MarketUnit(str(parent['dataset_id']),day,context['ticker'],int(parent['listing_key']),str(parent['attempt_id']))
    request=MarketRequest(unit,recipe_hash,source_hash,tuple(rules),{},'0'*64,context['predecessor_hash'],close,
        True,adjustment_hash=context['adjustment_hash'])
    if request.parent('bars')!=context['bars_parent_hash']:raise ValueError('V7 seed bar lineage differs')
    publisher=Publisher(query,database,claim)
    bars_scope=Scope(unit.dataset_id,day,unit.listing_key,'bars',context['bars_parent_hash'])
    bars=publisher.receipt(bars_scope)
    if bars is None:raise ValueError('V7 seed bars are not certified')
    expected=request.parent('v7',(bars['output_hash'],))
    if expected!=parent['parent_hash']:raise ValueError('V7 seed receipt lineage differs')
    scope=Scope(unit.dataset_id,day,unit.listing_key,'v7',expected)
    receipt=publisher.receipt(scope)
    locator={k:v for k,v in parent.items() if k!='state_context'}
    if receipt is None or receipt!=locator:raise ValueError('V7 seed locator differs from stored authority')
    checkpoint,_=load_from_bars(query,database,claim,scope,bars_scope,recipe_hash,
        context['predecessor_hash'],unit.ticker,'AH',regular_close=close)
    return dict(receipt,state_context=dict(context)),checkpoint


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

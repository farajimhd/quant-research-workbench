"""Identity-only dated provider observations; never fundamental availability."""
from dataclasses import dataclass,asdict
from datetime import date,datetime,timezone,timedelta
import re
from .contracts import digest,identifier,literal


@dataclass(frozen=True)
class ListingObservation:
    as_of: date
    ticker: str
    composite_figi: str
    share_class_figi: str
    cik: str
    exchange: str
    currency: str
    provider_type: str
    active: bool
    list_date: date|None
    delisted_date: date|None

    def __post_init__(self):
        if (type(self.as_of) is not date or type(self.active) is not bool
                or not re.fullmatch(r'[A-Z0-9._-]{1,32}',self.ticker)
                or not re.fullmatch('[A-Z0-9]{12}',self.composite_figi)
                or (self.share_class_figi and not re.fullmatch('[A-Z0-9]{12}',self.share_class_figi))
                or (self.cik and not re.fullmatch('[0-9]{10}',self.cik))
                or not self.exchange or self.currency!='USD'
                or any(v is not None and type(v) is not date for v in (self.list_date,self.delisted_date))):
            raise ValueError('Invalid normalized historical identity observation')
        if ((self.list_date and self.list_date>self.as_of)
                or (self.list_date and self.delisted_date and self.list_date>self.delisted_date)
                or (self.active and self.delisted_date and self.delisted_date<=self.as_of)):
            raise ValueError('Historical lifecycle dates conflict with the observation')

    @property
    def evidence_hash(self):
        return digest(dict(version='provider-dated-listing-observation-v1',**asdict(self)))


def normalize(payload,as_of,requested_ticker):
    """Project only identity/lifecycle fields from a dated API response.

    A successful observation proves this date only. Interval reconstruction
    still requires dated endpoints and complete intervening change coverage.
    Current conids are research identity anchors, not historical execution proof.
    """
    if type(as_of) is not date or not isinstance(payload,dict):raise ValueError('Dated identity observation is required')
    ticker=str(payload.get('ticker') or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker) or ticker!=requested_ticker.strip().upper():
        raise ValueError('Historical overview returned a different or invalid ticker')
    if payload.get('market')!='stocks' or payload.get('locale')!='us':
        raise ValueError('Historical identity observation is outside US stock scope')
    figi=str(payload.get('composite_figi') or '').strip().upper()
    share=str(payload.get('share_class_figi') or '').strip().upper()
    if not re.fullmatch('[A-Z0-9]{12}',figi) or (share and not re.fullmatch('[A-Z0-9]{12}',share)):
        raise ValueError('Historical identity requires stable FIGI evidence')
    cik=str(payload.get('cik') or '').strip()
    if cik and (not re.fullmatch('[0-9]{1,10}',cik) or int(cik)==0):raise ValueError('Invalid historical issuer CIK')
    exchange=str(payload.get('primary_exchange') or '').strip().upper()
    currency=str(payload.get('currency_name') or '').strip().upper()
    if not exchange or currency!='USD' or type(payload.get('active')) is not bool:
        raise ValueError('Historical venue/currency/activity evidence is incomplete')
    listed=date.fromisoformat(payload['list_date']) if payload.get('list_date') else None
    delisted=date.fromisoformat(str(payload['delisted_utc'])[:10]) if payload.get('delisted_utc') else None
    if ((listed and listed>as_of) or (listed and delisted and listed>delisted)
            or (payload['active'] and delisted and delisted<=as_of)):
        raise ValueError('Historical lifecycle dates conflict with the observation')
    return ListingObservation(as_of,ticker,figi,share,cik.zfill(10) if cik else '',exchange,currency,
        str(payload.get('type') or ''),payload['active'],listed,delisted)


def reconcile(observation,*,expected_composite_figi,expected_cik=None):
    """Fail closed on ticker reuse or an unresolved issuer transition."""
    if observation.composite_figi!=expected_composite_figi.strip().upper():
        raise ValueError('Historical ticker belongs to a different security')
    if expected_cik and observation.cik!=str(expected_cik).zfill(10):
        raise ValueError('Historical issuer transition requires canonical resolution')
    return observation


TABLE='research_listing_observation_v1'
EPOCH=date(1970,1,1)


def persistence_table():
    from .schema import Table,c
    columns=(c('as_of','Date'),c('ticker','LowCardinality(String)'),c('composite_figi','FixedString(12)'),
        c('share_class_figi','LowCardinality(String)'),c('cik','LowCardinality(String)'),
        c('exchange','LowCardinality(String)'),c('currency','LowCardinality(String)'),
        c('provider_type','LowCardinality(String)'),c('active','UInt8'),
        c('list_day','Nullable(Int32)'),c('delisted_day','Nullable(Int32)'),
        c('evidence_hash','FixedString(64)'),c('observed_at',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'))
    return Table(TABLE,columns,('as_of','ticker'),'toYYYYMM(as_of)')


def row(observation):
    value=asdict(observation)
    value['as_of']=str(observation.as_of);value['active']=int(observation.active)
    value['list_day']=(observation.list_date-EPOCH).days if observation.list_date else None
    value['delisted_day']=(observation.delisted_date-EPOCH).days if observation.delisted_date else None
    del value['list_date'];del value['delisted_date']
    return dict(value,evidence_hash=observation.evidence_hash)


def publish(query,database,claim,observations):
    """Bounded batch, one shared dictionary owner, exact no-insert resume."""
    from .schema import verify_auxiliary_storage
    from .publication import DictionaryClaim,insert_rows
    identifier(database);observations=tuple(observations)
    if not 1<=len(observations)<=1000:raise ValueError('Identity observation batch must contain 1 to 1000 rows')
    if not database.startswith('certified_research_test_') and (not isinstance(claim,DictionaryClaim)
            or claim.dictionary_database!=database):
        raise ValueError('Historical observation publication requires global database ownership')
    desired=[row(o) for o in observations]
    keys=[(r['as_of'],r['ticker']) for r in desired]
    if len(set(keys))!=len(keys):raise ValueError('Duplicate historical ticker/date observations')
    claim.check();verify_auxiliary_storage(query,database,(TABLE,))
    table=persistence_table()
    metadata=query(f'SELECT engine,sorting_key,partition_key FROM system.tables WHERE database={literal(database)} AND name={literal(TABLE)}')
    if metadata!=[dict(engine='MergeTree',sorting_key='as_of, ticker',partition_key='toYYYYMM(as_of)')]:
        raise ValueError('Historical identity engine/key contract differs')
    columns=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} AND table={literal(TABLE)} ORDER BY position')
    expected=[(c.name,c.type.replace(' ',''),'CODEC('+c.codec.replace(' ','')+')') for c in table.columns]
    if [(r['name'],r['type'].replace(' ',''),r['compression_codec'].replace(' ','')) for r in columns]!=expected:
        raise ValueError('Historical identity column/codec contract differs')
    fields=','.join(c.name for c in table.columns if c.name!='observed_at')
    predicate=','.join(f'(toDate({literal(d)}),{literal(t)})' for d,t in keys)
    sql=f'SELECT {fields} FROM {database}.{TABLE} WHERE (as_of,ticker) IN ({predicate})'
    actual=query(sql);wanted={k:r for k,r in zip(keys,desired)};found=set()
    for item in actual:
        key=(str(item['as_of']),item['ticker'])
        item=dict(item,active=int(item['active']),list_day=None if item['list_day'] is None else int(item['list_day']),
            delisted_day=None if item['delisted_day'] is None else int(item['delisted_day']))
        if key in found or wanted.get(key)!=item:raise ValueError('Historical identity conflicts with immutable stored evidence')
        found.add(key)
    missing=[dict(r,observed_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')) for k,r in wanted.items() if k not in found]
    if missing:
        claim.check();insert_rows(query,database,TABLE,missing)
        if sorted(query(sql),key=lambda r:(r['as_of'],r['ticker']))!=sorted(desired,key=lambda r:(r['as_of'],r['ticker'])):
            raise ValueError('Historical identity insert did not read back exactly')
    claim.check()
    verify_auxiliary_storage(query,database,(TABLE,))
    return dict(rows=len(desired),inserted=len(missing),evidence_hash=digest(sorted(desired,key=lambda r:(r['as_of'],r['ticker']))))


def load(query,database,as_of,ticker):
    """Read a dated point and independently reconstruct its identity hash."""
    from .schema import verify_auxiliary_storage
    identifier(database)
    if type(as_of) is not date or not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker):raise ValueError('Invalid historical identity lookup')
    verify_auxiliary_storage(query,database,(TABLE,))
    rows=query(f'SELECT * FROM {database}.{TABLE} WHERE as_of=toDate({literal(as_of)}) AND ticker={literal(ticker)}')
    if not rows:return None
    if len(rows)!=1:raise ValueError('Historical identity point is duplicated')
    value=rows[0]
    if int(value['active']) not in (0,1):raise ValueError('Invalid stored historical activity state')
    observation=ListingObservation(date.fromisoformat(str(value['as_of'])),value['ticker'],value['composite_figi'],
        value['share_class_figi'],value['cik'],value['exchange'],value['currency'],value['provider_type'],bool(int(value['active'])),
        None if value['list_day'] is None else EPOCH+timedelta(days=int(value['list_day'])),
        None if value['delisted_day'] is None else EPOCH+timedelta(days=int(value['delisted_day'])))
    if observation.evidence_hash!=value['evidence_hash']:raise ValueError('Stored historical identity evidence hash differs')
    return observation


class AcquisitionHandlers:
    """One provider request per global executor unit; no nested worker pool."""
    def __init__(self,database,claim,client_factory,provider_factory,dictionary_claim_factory,source_guard):
        identifier(database)
        self.database=database;self.claim=claim;self.clients=client_factory;self.providers=provider_factory
        self.dictionaries=dictionary_claim_factory;self.guard=source_guard

    def inputs(self,plan):
        if plan.unit.product!='identity_observation':raise ValueError('Wrong historical identity acquisition product')
        args=plan.arguments
        if not {'as_of','ticker','composite_figi'}<=set(args)<= {'as_of','ticker','composite_figi','cik'}:
            raise ValueError('Unexpected historical identity acquisition fields')
        day=date.fromisoformat(args['as_of']);ticker=args['ticker'];figi=args['composite_figi'];cik=args.get('cik')
        if (not isinstance(ticker,str) or not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker)
                or not isinstance(figi,str) or not re.fullmatch('[A-Z0-9]{12}',figi)
                or (cik is not None and not re.fullmatch('[0-9]{1,10}',str(cik)))):
            raise ValueError('Invalid historical identity acquisition arguments')
        return day,ticker,figi,cik

    def verifier(self,plan,parents):
        day,ticker,figi,cik=self.inputs(plan);client=self.clients()
        try:
            self.claim.check();self.guard();observation=load(client.query,self.database,day,ticker)
            if observation is None:return None
            reconcile(observation,expected_composite_figi=figi,expected_cik=cik)
            self.claim.check();self.guard()
            return dict(output_hash=observation.evidence_hash,as_of=str(day),ticker=ticker,composite_figi=figi)
        finally:client.close()

    def producer(self,plan,parents,stop):
        if stop.is_set():raise InterruptedError('Historical identity acquisition stopped')
        if self.verifier(plan,parents) is not None:return
        day,ticker,figi,cik=self.inputs(plan)
        self.claim.check();self.guard()
        payload=self.providers().fetch_ticker_overview(ticker,as_of=day)
        observation=reconcile(normalize(payload,day,ticker),expected_composite_figi=figi,expected_cik=cik)
        self.claim.check();self.guard()
        if stop.is_set():raise InterruptedError('Historical identity stopped before publication')
        client=self.clients();dictionary=None
        try:
            dictionary=self.dictionaries()
            if getattr(dictionary,'dictionary_database',None)!=self.database:
                raise ValueError('Historical identity dictionary ownership is for another database')
            self.claim.check();self.guard()
            publish(client.query,self.database,dictionary,[observation])
            self.claim.check();self.guard()
        finally:
            if dictionary is not None:dictionary.close()
            client.close()

    def registry(self):return {'identity_observation':self.producer},{'identity_observation':self.verifier}


def prepare_observations(database,claim,client_factory,provider_factory,dictionary_claim_factory,source_guard,
                         requests,*,acquire_missing=False,workers=4,stop=None,progress=None):
    """Bounded point acquisition/reuse; no interval interpolation or SIP REST.

    Existing points verify before provider construction. Missing points remain
    blocked unless acquisition is explicitly enabled after resubscription.
    """
    from threading import Event
    from itertools import islice
    from .campaign import PlannedUnit
    from .execution import Unit,execute
    requests=tuple(islice(iter(requests),1001))
    if not requests or len(requests)>1000 or type(acquire_missing) is not bool:
        raise ValueError('Historical identity batch requires 1 to 1000 explicit requests')
    registry=AcquisitionHandlers(database,claim,client_factory,provider_factory,dictionary_claim_factory,source_guard)
    plans=[]
    for index,args in enumerate(requests):
        plan=PlannedUnit(Unit('identity:'+str(index),'identity_observation'),dict(args))
        registry.inputs(plan);plans.append(plan)
    points=[(p.arguments['as_of'],p.arguments['ticker']) for p in plans]
    if len(points)!=len(set(points)):raise ValueError('Duplicate historical identity point requests')
    by_key={p.unit.key:p for p in plans};signal=stop if stop is not None else Event()
    def reuse(unit,parents):return registry.verifier(by_key[unit.key],{})
    def produce(unit,parents,signal):
        if not acquire_missing:raise ValueError('Missing dated identity point; provider acquisition is not enabled')
        plan=by_key[unit.key];registry.producer(plan,{},signal)
        proof=registry.verifier(plan,{})
        if proof is None:raise ValueError('Historical identity publication did not independently verify')
        return proof
    return execute(tuple(p.unit for p in plans),{'identity_observation':produce},workers=workers,
        reuse=reuse,stop=signal,progress=progress)

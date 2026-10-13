"""Identity-only dated provider observations; never fundamental availability."""
from dataclasses import dataclass,asdict
from datetime import date,datetime,timezone
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

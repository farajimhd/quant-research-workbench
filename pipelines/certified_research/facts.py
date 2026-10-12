"""Versioned metric dictionaries and batch public-as-of fact access.

Current app catalogs are parsed as deterministic literals rather than importing
UI service initialization or maintaining a second hand-copied tag list.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from math import isfinite
from uuid import UUID

from pipelines.reference_data.security_dimensions import SEC_XBRL_DIMENSIONS
from .contracts import digest,identifier,literal

VALID=1
UNAVAILABLE=2
CONFLICT=3
EXACT_TIME=1
CONSERVATIVE_TIME=2
UNITS={'USD':1,'shares':2,'ratio':3,'days':4,'USD/share':5,'percent':6,'shares/day':7}


@dataclass(frozen=True)
class Metric:
    name: str
    unit: str
    tags: tuple[str,...]=()
    formula: str='reported'

    @property
    def key(self):
        number=int.from_bytes(sha256(('research-metric-v1\x00'+self.name).encode()).digest()[:4],'big')
        if number==0:raise ValueError('Reserved metric ID; catalog revision required')
        return number


def catalog():
    root=Path(__file__).resolve().parents[2]
    tree=ast.parse((root/'src/backend/ticker_facts_service.py').read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name)
              and n.target.id=='FUNDAMENTAL_TAGS')
    app=ast.literal_eval(node.value)
    by_name={m.name:m for m in [Metric(code,unit,(tag,)) for code,_,_,tag,unit,_,_ in SEC_XBRL_DIMENSIONS]}
    unit_by_tag={tag:unit for _,_,_,tag,unit,_,_ in SEC_XBRL_DIMENSIONS}
    for name,tags in app:
        units={unit_by_tag[t] for t in tags if t in unit_by_tag}
        unit=next(iter(units)) if len(units)==1 else 'shares' if 'shares' in name.lower() else 'USD/share' if 'EPS' in name or name=='Dividends per share' else 'ratio' if name=='Effective tax rate' else 'USD'
        if len(units)>1:raise ValueError('App catalog mixes incompatible units')
        key='fundamental.'+name.lower().replace(' ','_').replace('&','and')
        by_name[key]=Metric(key,unit,tuple(tags))
    for name,unit in [('float.reported','shares'),('float.estimated','shares'),('float.upper_bound','shares'),
            ('shares.outstanding','shares'),('short.volume','shares'),('short.exempt_volume','shares'),
            ('short.non_exempt_volume','shares'),('short.total_volume','shares'),('short.interest','shares'),
            ('short.provider_adv','shares/day'),('short.provider_days_to_cover','days')]:
        by_name[name]=Metric(name,unit)
    by_name['short.volume_ratio']=Metric('short.volume_ratio','ratio',formula='short.volume/short.total_volume; same source venue')
    by_name['short.interest_float_ratio']=Metric('short.interest_float_ratio','ratio',formula='short.interest/qualified_float; matching share basis')
    values=tuple(sorted(by_name.values(),key=lambda m:m.name))
    if len({m.key for m in values})!=len(values) or any(m.unit not in UNITS for m in values):
        raise ValueError('Metric-key collision or undefined unit')
    return values


def catalog_hash(metrics=None):
    return digest(dict(version='research-metric-v1',units=UNITS,
                       metrics=[asdict(m) for m in (metrics or catalog())]))


def conservative_date_availability(day):
    """Date-only release is not usable until the following UTC day.

    Call only when an authoritative source proves that UTC publication date.
    This is a conservative bound, not fabricated timestamp precision.
    """
    from datetime import date,timedelta,time
    if type(day) is not date:raise ValueError('Publication date must be an authoritative date')
    return datetime.combine(day+timedelta(days=1),time(),timezone.utc)


def verify_fact(row):
    UUID(row.get('dataset_id',''))
    if (row.get('entity_kind') not in (1,2,3)
            or not isinstance(row.get('available_at'),datetime) or row['available_at'].tzinfo is None
            or not 0<=row.get('confidence',-1)<=100
            or not isinstance(row.get('evidence_hash'),str)
            or len(row['evidence_hash'])!=64 or any(c not in '0123456789abcdef' for c in row['evidence_hash'])):
        raise ValueError('Fact lacks public-time, identity or provenance evidence')
    if (row.get('status') not in (VALID,UNAVAILABLE,CONFLICT)
            or row.get('precision') not in (EXACT_TIME,CONSERVATIVE_TIME)
            or row.get('unit_key') not in UNITS.values()):
        raise ValueError('Invalid fact status, availability precision or unit')
    value=row.get('value')
    if row['status']==VALID and (value is None or isinstance(value,bool) or not isfinite(value)):
        raise ValueError('Valid fact requires finite authoritative value')
    if row['status']!=VALID and (value is not None or not row.get('reason')):
        raise ValueError('Unavailable/conflicting fact needs null and explicit reason')
    for key in ('entity_key','metric_key','revision'):
        if type(row.get(key)) is not int or row[key]<(0 if key=='revision' else 1):raise ValueError('Invalid fact identity/revision')


def lookup_sql(database,dataset_id,entities,metrics,cutoff,*,strict_prior=False):
    identifier(database);UUID(dataset_id)
    if cutoff.tzinfo is None or not entities or not metrics or len(entities)>1024 or len(metrics)>256:
        raise ValueError('Invalid bounded as-of lookup')
    if any(type(k) is not int or type(v) is not int or k not in (1,2,3) or not 0<v<2**64 for k,v in entities):
        raise ValueError('Invalid entity lookup')
    if any(type(v) is not int or not 0<v<2**32 for v in metrics):raise ValueError('Invalid metric lookup')
    entity_list=','.join(f'({k},{v})' for k,v in sorted(set(entities)))
    metric_list=','.join(str(m) for m in sorted(set(metrics)))
    stamp=literal(cutoff.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
    comparator='<' if strict_prior else '<='
    return (f'SELECT * FROM {database}.research_facts_v1 WHERE dataset_id=toUUID({literal(dataset_id)}) '
            f'AND (entity_kind,entity_key) IN ({entity_list}) AND metric_key IN ({metric_list}) '
            f'AND available_at{comparator}toDateTime64({stamp},6,\'UTC\') '
            'ORDER BY entity_kind,entity_key,metric_key,period_end DESC NULLS LAST,available_at DESC,revision DESC '
            'LIMIT 1 BY entity_kind,entity_key,metric_key')

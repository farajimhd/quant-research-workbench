"""Immutable ClickHouse campaign plans, verified resume and final root seals.

Producer implementations and their independent verifiers are registered by the
launcher. Successful callbacks alone never certify a campaign. Root verification
re-reads every unit's authority and source epoch; progress history is not proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timezone
from hashlib import sha256
import json,re
from collections import deque
from threading import RLock
from uuid import UUID

from .contracts import digest,identifier,literal
from .execution import Unit,execute,validate
from .publication import insert_rows
from .schema import schema_hash,verify_storage

STATES={'active':1,'certified':2,'reused':3,'failed':4,'blocked':5,'interrupted':6}


def status(query,database,dataset_id):
    identifier(database);UUID(dataset_id);verify_storage(query,database,require_tables=True)
    where=f'dataset_id=toUUID({literal(dataset_id)})'
    definitions=query(f'SELECT contract_hash,start_date,end_date FROM {database}.research_dataset_v1 WHERE {where}')
    if len(definitions)!=1:raise ValueError('Dataset definition is missing or duplicated')
    counts=query(f'SELECT s.status status,count() units FROM {database}.research_campaign_unit_v1 p LEFT JOIN '
        f'(SELECT dataset_id,unit_id,argMax(status,sequence) status FROM {database}.research_campaign_state_v1 '
        f'WHERE {where} GROUP BY dataset_id,unit_id) s USING (dataset_id,unit_id) WHERE p.{where} GROUP BY s.status')
    names={0:'queued',**{v:k for k,v in STATES.items()}}
    seals=query(f'SELECT plan_hash,root_hash,units,published_at FROM {database}.research_campaign_seal_v1 WHERE {where}')
    if len(seals)>1:raise ValueError('Campaign seals are duplicated')
    return dict(dataset_id=dataset_id,definition=definitions[0],counts={names[int(r['status'])]:int(r['units']) for r in counts},
                seal=seals[0] if seals else None,inspection_only=True)


def unit_id(key):
    if not isinstance(key,str) or not key or len(key.encode())>160:raise ValueError('Invalid bounded campaign unit key')
    result=int.from_bytes(sha256(('research-unit-v1\0'+key).encode()).digest()[:8],'big')
    if not result:raise ValueError('Reserved unit ID; revise the explicit key')
    return result


def encoded(value):
    def inspect(v):
        if isinstance(v,dict):
            if any(str(k).lower() in {'password','api_key','access_token','secret','credentials'} for k in v):
                raise ValueError('Secrets cannot enter a campaign plan')
            for child in v.values():inspect(child)
        elif isinstance(v,(tuple,list)):
            for child in v:inspect(child)
    inspect(value)
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,default=str)


@dataclass(frozen=True)
class PlannedUnit:
    unit: Unit
    arguments: dict

    def __post_init__(self):
        unit_id(self.unit.key)
        if not isinstance(self.arguments,dict) or len(encoded(self.arguments).encode())>8192 or len(self.unit.parents)>1024:
            raise ValueError('Unit arguments/dependencies exceed metadata bounds')

    def row(self,dataset):
        body=encoded(self.arguments)
        return dict(dataset_id=dataset,unit_id=unit_id(self.unit.key),unit_key=self.unit.key,product=self.unit.product,
            parents=[unit_id(k) for k in self.unit.parents],arguments=body,arguments_hash=digest(self.arguments),memory_bytes=self.unit.memory_bytes)


class Campaign:
    def __init__(self,query,database,claim,dataset_id,config,units,source_hash,source_guard,producers,verifiers):
        identifier(database);UUID(dataset_id)
        if not re.fullmatch('[0-9a-f]{64}',source_hash) or not units:raise ValueError('Campaign source/units are absent')
        self.query=query;self.database=database;self.claim=claim;self.dataset=dataset_id;self.config=config
        self.units=tuple(units);self.source_hash=source_hash;self.source_guard=source_guard
        self.producers=producers;self.verifiers=verifiers;self.by_key={p.unit.key:p for p in self.units}
        validate(tuple(p.unit for p in self.units),config.global_memory_bytes)
        if len({unit_id(k) for k in self.by_key})!=len(self.by_key):raise ValueError('Campaign unit ID collision')
        if any(p.unit.product not in producers or p.unit.product not in verifiers for p in units):
            raise ValueError('Campaign producer/authority verifier is missing')
        self.rows=tuple(p.row(dataset_id) for p in units)
        self.definition=dict(config=config.identity(),source_hash=source_hash,
            units_hash=digest(sorted(self.rows,key=lambda r:r['unit_id'])),unit_count=len(units))
        self.plan_hash=digest(self.definition);self.where=f'dataset_id=toUUID({literal(dataset_id)})'
        self.sequence=0
        self.guard_lock=RLock()
        children={k:[] for k in self.by_key};degree={k:len(p.unit.parents) for k,p in self.by_key.items()}
        for p in self.units:
            for parent in p.unit.parents:children[parent].append(p.unit.key)
        ready=deque(k for k,n in degree.items() if not n);self.order=[]
        while ready:
            key=ready.popleft();self.order.append(key)
            for child in children[key]:
                degree[child]-=1
                if degree[child]==0:ready.append(child)

    def guard(self):
        with self.guard_lock:self.claim.check();self.source_guard()

    def install(self,*,allow_writes=True):
        self.guard();verify_storage(self.query,self.database,require_tables=True)
        existing=self.query(f'SELECT contract_hash,schema_hash,definition,start_date,end_date FROM {self.database}.research_dataset_v1 WHERE {self.where}')
        expected=dict(contract_hash=self.plan_hash,schema_hash=schema_hash(),definition=encoded(self.definition),
                      start_date=str(self.config.start),end_date=str(self.config.end))
        if existing and existing!=[expected]:raise ValueError('Dataset definition is duplicated or changed; use a new immutable campaign')
        if not existing:
            if not allow_writes:raise ValueError('Campaign definition has not been installed')
            row=dict(dataset_id=self.dataset,**expected,created_at=self.now())
            insert_rows(self.query,self.database,'research_dataset_v1',[row]);self.guard()
        for offset in range(0,len(self.rows),128):
            batch=self.rows[offset:offset+128];ids=','.join(str(r['unit_id']) for r in batch)
            sql=f'SELECT * FROM {self.database}.research_campaign_unit_v1 WHERE {self.where} AND unit_id IN ({ids}) ORDER BY unit_id'
            actual=self.query(sql);wanted={r['unit_id']:r for r in batch};found={}
            for row in actual:
                key=int(row['unit_id']);normalized=dict(row,unit_id=key,memory_bytes=int(row['memory_bytes']),parents=[int(p) for p in row['parents']])
                if key in found or wanted.get(key)!=normalized:raise ValueError('Campaign plan unit is duplicated or changed')
                found[key]=normalized
            missing=[r for r in batch if r['unit_id'] not in found]
            if missing:
                if not allow_writes:raise ValueError('Campaign plan has missing units')
                self.guard();insert_rows(self.query,self.database,'research_campaign_unit_v1',missing)
                read=self.query(sql)
                normalized=[dict(r,unit_id=int(r['unit_id']),memory_bytes=int(r['memory_bytes']),parents=[int(p) for p in r['parents']]) for r in read]
                if sorted(normalized,key=lambda r:r['unit_id'])!=sorted(batch,key=lambda r:r['unit_id']):
                    raise ValueError('Campaign plan insert did not read back exactly')
        counts=self.query(f'SELECT count() n,uniqExact(unit_id) unique_ids FROM {self.database}.research_campaign_unit_v1 WHERE {self.where}')[0]
        if int(counts['n'])!=len(self.rows) or int(counts['unique_ids'])!=len(self.rows):raise ValueError('Campaign plan has extra/missing units')
        self.sequence=int(self.query(f'SELECT ifNull(max(sequence),0) n FROM {self.database}.research_campaign_state_v1 WHERE {self.where}')[0]['n'])
        self.guard()

    @staticmethod
    def now():return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')

    def authority(self,unit,parents):
        self.guard()
        result=self.verifiers[unit.product](self.by_key[unit.key],parents)
        if result is not None and (not isinstance(result,dict) or not re.fullmatch('[0-9a-f]{64}',result.get('output_hash',''))):
            raise ValueError('Unit authority verifier did not return a valid output fingerprint')
        return result

    def run(self,*,stop=None,progress=None):
        self.guard();verify_storage(self.query,self.database,require_tables=True)
        if self.query(f'SELECT count() n FROM {self.database}.research_campaign_seal_v1 WHERE {self.where}')[0]['n']:
            return dict(status='complete',units={},results={},errors={},seal=self.seal())
        self.install()
        def event(change):
            self.claim.check();self.sequence+=1
            insert_rows(self.query,self.database,'research_campaign_state_v1',[dict(dataset_id=self.dataset,
                unit_id=unit_id(change['unit']),sequence=self.sequence,status=STATES[change['status']],output_hash=change.get('output_hash') or '0'*64,
                reason=change['reason'][:256],changed_at=self.now())])
            if progress:progress(change)
        def produce(unit,parents,stop):
            self.guard();self.producers[unit.product](self.by_key[unit.key],parents,stop)
            result=self.authority(unit,parents)
            if result is None:raise ValueError('Producer completed without independently verified authority')
            return result
        result=execute(tuple(p.unit for p in self.units),{kind:produce for kind in self.producers},
            workers=self.config.workers,memory_bytes=self.config.global_memory_bytes,reuse=self.authority,
            progress=event,stop=stop)
        if result['status']=='complete':result['seal']=self.seal()
        return result

    def verify(self):
        self.install(allow_writes=False);outputs={}
        # Verification does not produce data; every authority is re-read.
        for key in self.order:
            unit=self.by_key[key].unit;receipt=self.authority(unit,{p:outputs[p] for p in unit.parents})
            if receipt is None:raise ValueError('Campaign unit has no certified authority: '+key)
            outputs[key]=receipt
        self.guard()
        return digest(dict(plan_hash=self.plan_hash,source_hash=self.source_hash,
            outputs={k:v['output_hash'] for k,v in sorted(outputs.items())}))

    def seal(self):
        root=self.verify()
        old=self.query(f'SELECT plan_hash,source_hash,root_hash,units FROM {self.database}.research_campaign_seal_v1 WHERE {self.where}')
        expected=dict(plan_hash=self.plan_hash,source_hash=self.source_hash,root_hash=root,units=len(self.units))
        if old:
            if len(old)!=1 or dict(old[0],units=int(old[0]['units']))!=expected:raise ValueError('Campaign seal is duplicated or no longer matches authority')
            return expected
        self.guard();insert_rows(self.query,self.database,'research_campaign_seal_v1',
            [dict(dataset_id=self.dataset,**expected,published_at=self.now())])
        after=self.query(f'SELECT plan_hash,source_hash,root_hash,units FROM {self.database}.research_campaign_seal_v1 WHERE {self.where}')
        if len(after)!=1 or dict(after[0],units=int(after[0]['units']))!=expected:raise ValueError('Campaign seal readback failed')
        self.guard();return expected

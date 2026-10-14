"""Normalized immutable scope locators; no certification assertions are stored."""
from datetime import date
import json
import re

from . import schema,sec_share_capture,sec_fundamental_publication,sec_scope_parity
from .contracts import digest,literal
from .publication import DictionaryClaim


def tables():
    c=schema.c
    return (
        schema.Table('research_sec_scope_document_v1',(c('specification_hash','FixedString(64)'),
            c('accession','FixedString(20)'),c('document','String'),c('reconciliation_hash','FixedString(64)'),c('reported_binding_hash','FixedString(64)')),
            ('specification_hash','accession','document'),'cityHash64(specification_hash)%32'),
        schema.Table('research_sec_scope_specification_v1',(c('specification_hash','FixedString(64)'),
            c('version','LowCardinality(String)'),c('entity_key','UInt64','T64,ZSTD(1)'),c('cik','String'),
            c('start','Date'),c('end','Date'),c('context_start','Date'),
            c('source_hash','FixedString(64)'),c('metrics','Array(String)'),c('forms','Array(String)'),c('bindings','UInt16','T64,ZSTD(1)')),
            ('specification_hash',),'toYYYYMM(start)'))


def records(specification):
    spec=json.loads(json.dumps(specification));body=dict(spec);sealed=body.pop('specification_hash',None)
    if sealed!=digest(body) or spec.get('version')!='sec-original-scope-specification-v1':
        raise ValueError('Immutable SEC scope specification fingerprint required')
    required={'version','entity_key','cik','start','end','context_start','source_hash','metrics','forms','bindings','specification_hash'}
    if set(spec)!=required or type(spec['entity_key']) is not int or not 0<spec['entity_key']<2**64:
        raise ValueError('Unsupported original SEC specification fields or issuer key')
    if (not re.fullmatch('0*[1-9][0-9]{0,9}',spec['cik']) or not re.fullmatch('[0-9a-f]{64}',spec['source_hash'])
            or not date.fromisoformat(spec['context_start'])<=date.fromisoformat(spec['start'])<=date.fromisoformat(spec['end'])):
        raise ValueError('Invalid original SEC specification identity or range')
    for key,limit in (('metrics',1000),('forms',32)):
        if (not isinstance(spec[key],list) or not 1<=len(spec[key])<=limit or len(set(spec[key]))!=len(spec[key])
                or any(not isinstance(v,str) or not v or len(v)>256 for v in spec[key])):
            raise ValueError('Invalid original SEC specification selection')
    bindings=spec['bindings']
    if not isinstance(bindings,list) or not 1<=len(bindings)<=1000:raise ValueError('Invalid original SEC document count')
    if bindings!=sorted(bindings,key=lambda r:(r['accession'],r['document'])):
        raise ValueError('Original SEC bindings require deterministic accession/document ordering')
    rows=[];seen=set()
    for item in bindings:
        if (set(item)!={'accession','document','reconciliation_hash','reported_binding_hash'}
                or not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}',item['accession'])
                or not isinstance(item['document'],str) or not 1<=len(item['document'])<=256
                or any(not re.fullmatch('[0-9a-f]{64}',item[k]) for k in ('reconciliation_hash','reported_binding_hash'))):
            raise ValueError('Invalid original SEC document locator')
        key=(item['accession'],item['document'])
        if key in seen:raise ValueError('Duplicate original SEC document identity')
        seen.add(key);rows.append(dict(specification_hash=sealed,**item))
    header={k:v for k,v in spec.items() if k!='bindings'};header['bindings']=len(rows)
    return tuple(rows),(header,)


def install(query,database,claim):
    if not isinstance(claim,DictionaryClaim) or claim.dictionary_database!=database:
        raise ValueError('Original SEC scope schema requires dictionary ownership')
    claim.check();sec_share_capture.verify(query,database,catalog=tables())
    for table in tables():claim.check();query(table.ddl(database),read=False)
    sec_share_capture.verify(query,database,required=True,catalog=tables());claim.check()


def capture(query,database,specification,claim,source_guard,admission):
    catalog=tables();groups=records(specification);key=specification['specification_hash']
    result=sec_fundamental_publication.capture_groups(query,database,catalog,groups,
        {t.name:('specification_hash',key) for t in catalog},claim,source_guard,admission)
    return dict(result,specification_hash=key,certified=False)


def load(query,database,key,claim,source_guard):
    if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('Invalid original SEC specification locator')
    claim.check();source_guard();catalog=tables();sec_share_capture.verify(query,database,required=True,catalog=catalog)
    def read(table,limit):
        columns=','.join(c.name for c in table.columns);base=f'FROM {database}.{table.name} WHERE specification_hash={literal(key)}'
        sizes=query(f'SELECT count() n,sum(length(toJSONString(tuple({columns})))) bytes {base}')
        if len(sizes)!=1 or int(sizes[0]['n'])>limit or int(sizes[0]['bytes'])>1024**2:
            raise ValueError('Original SEC specification exceeds row/byte bounds')
        rows=query(f'SELECT {columns} {base} ORDER BY {",".join(table.order)} LIMIT {limit+1}')
        if len(rows)!=int(sizes[0]['n']):raise ValueError('Original SEC specification changed during read')
        return rows
    headers=read(catalog[1],1)
    if len(headers)!=1:raise ValueError('Original SEC specification header is missing or ambiguous')
    rows=read(catalog[0],1000);header=dict(headers[0]);count=int(header.pop('bindings'));header['entity_key']=int(header['entity_key'])
    spec=dict(header,bindings=[{k:v for k,v in row.items() if k!='specification_hash'} for row in rows])
    expected=records(spec)
    if count!=len(rows) or rows!=list(expected[0]) or dict(headers[0],entity_key=int(headers[0]['entity_key']),bindings=count)!=expected[1][0]:
        raise ValueError('Original SEC specification normalized rows differ from fingerprint')
    claim.check();source_guard()
    if read(catalog[1],1)!=headers or read(catalog[0],1000)!=rows:raise ValueError('Original SEC specification changed during verification')
    claim.check();return spec


class StoredGuard:
    def __init__(self,query,plans,claim,source_guard):
        self.locators={};self.query=query;self.claim=claim;self.source_guard=source_guard
        for plan in plans:
            if plan.unit.product!='fundamentals':continue
            a=plan.arguments;key=(int(a['entity_key']),a['start'],a['end'],a['source_hash'])
            locator=a.get('original_specification_hash')
            if key in self.locators or not isinstance(locator,str) or not re.fullmatch('[0-9a-f]{64}',locator):
                raise ValueError('Fundamental plan requires one frozen original SEC specification locator per scope')
            self.locators[key]=locator

    def __call__(self,scope):
        key=(scope.entity_key,str(scope.start),str(scope.end),scope.parent_hash)
        if key not in self.locators:raise ValueError('Original SEC specification is absent from frozen plan')
        spec=load(self.query,'q_live',self.locators[key],self.claim,self.source_guard)
        result=sec_scope_parity.verify(self.query,scope,spec,self.claim,self.source_guard)
        if load(self.query,'q_live',self.locators[key],self.claim,self.source_guard)!=spec:
            raise ValueError('Original SEC scope specification changed during parity verification')
        return result

"""Immutable normalized archive receipts and conservative document-time witnesses."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import json

from . import schema,sec_share_capture,sec_public_archive_time
from .contracts import digest,literal
from .publication import insert_rows,DictionaryClaim


def tables():
    c=schema.c
    return (
        schema.Table('research_sec_archive_download_receipt_v1',(
            c('receipt_hash','FixedString(64)'),c('source_date','Date'),c('container_sha256','FixedString(64)'),
            c('source_url','String'),c('downloaded_at_utc',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'),
            c('last_modified_utc',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'),c('byte_size','UInt64','T64,ZSTD(1)'),
            c('proof','String')),('receipt_hash',),'toYYYYMM(source_date)'),
        schema.Table('research_sec_document_archive_time_v1',(
            c('document_key','FixedString(64)'),c('source_archive_date','Date'),c('policy_version','LowCardinality(String)'),
            c('mode','LowCardinality(String)'),c('witness_hash','FixedString(64)'),c('qualification_hash','FixedString(64)'),
            c('receipt_hash','FixedString(64)'),c('container_sha256','FixedString(64)'),c('submission_hash','FixedString(64)'),
            c('document_hash','FixedString(64)'),c('archive_version_key','FixedString(64)'),
            c('member_ordinal','UInt32','T64,ZSTD(1)'),c('available_at',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'),
            c('precision','UInt8'),c('proof','String')),('document_key','policy_version','mode'),'toYYYYMM(source_archive_date)'))


def install(query,database,claim):
    if not isinstance(claim,DictionaryClaim) or claim.dictionary_database!=database:
        raise ValueError('SEC witness schema needs global dictionary ownership')
    claim.check();sec_share_capture.verify(query,database,catalog=tables())
    for table in tables():claim.check();query(table.ddl(database),read=False)
    sec_share_capture.verify(query,database,required=True,catalog=tables());claim.check()


def records(witnesses,qualifications,receipt):
    if (not isinstance(witnesses,(tuple,list)) or not 1<=len(witnesses)<=1000
            or not isinstance(qualifications,(tuple,list)) or len(qualifications)!=len(witnesses)):
        raise ValueError('SEC witness publication needs a bounded complete group')
    def timestamp(value):return value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    documents=[];seen=set();receipt_hash=None
    for witness,qualified in zip(witnesses,qualifications):
        actual=sec_public_archive_time.qualify(witness,receipt,mode=qualified['mode'])
        if actual!=qualified:raise ValueError('SEC qualification differs from independently checked source evidence')
        key=(witness['document_key'],qualified['version'],qualified['mode'])
        if key in seen:raise ValueError('SEC witness group has duplicate document/policy/mode keys')
        seen.add(key)
        if receipt_hash is not None and receipt_hash!=qualified['receipt_hash']:
            raise ValueError('SEC group contains competing archive receipt identities')
        receipt_hash=qualified['receipt_hash']
        documents.append(dict(document_key=key[0],source_archive_date=witness['source_archive_date'],policy_version=key[1],
            mode=key[2],witness_hash=witness['witness_hash'],qualification_hash=qualified['qualification_hash'],
            receipt_hash=receipt_hash,container_sha256=witness['container_sha256'],submission_hash=witness['submission_hash'],
            document_hash=witness['document_hash'],archive_version_key=witness['archive_version_key'],member_ordinal=witness['member_ordinal'],
            available_at=timestamp(datetime.fromisoformat(qualified['available_at'])),precision=qualified['precision'],
            proof=json.dumps(dict(witness=witness,qualification=qualified),sort_keys=True,separators=(',',':'))))
    source={k:receipt[k] for k in ('source_kind','source_url','source_date','downloaded_at_utc','byte_size','sha256','last_modified','status')}
    archive=dict(receipt_hash=receipt_hash,source_date=receipt['source_date'],container_sha256=receipt['sha256'],source_url=receipt['source_url'],
        downloaded_at_utc=timestamp(datetime.fromisoformat(receipt['downloaded_at_utc'].replace('Z','+00:00'))),
        last_modified_utc=timestamp(parsedate_to_datetime(receipt['last_modified'])),byte_size=receipt['byte_size'],
        proof=json.dumps(source,sort_keys=True,separators=(',',':')))
    return (archive,),tuple(documents)


class Publisher:
    def __init__(self,query,database,claim,source_guard,admission):
        if not callable(admission):raise ValueError('SEC witness publication requires source-write admission')
        self.query=query;self.database=database;self.claim=claim;self.source_guard=source_guard;self.admission=admission

    def reuse(self,prepared,receipt,*,mode):
        """Read independently qualified complete evidence; partial groups need execution."""
        if not isinstance(prepared,(tuple,list)) or not 1<=len(prepared)<=1000:
            raise ValueError('SEC reuse requires a bounded prepared group')
        self.claim.check();self.source_guard()
        sec_share_capture.verify(self.query,self.database,required=True,catalog=tables())
        witnesses=[];qualified=[]
        for source in prepared:
            body=dict(source);fingerprint=body.pop('prepared_hash',None)
            if fingerprint!=digest(body):raise ValueError('SEC reuse prepared fingerprint differs')
            document=source['document'];lineage=document['lineage']
            rows=self.query(f'SELECT proof FROM {self.database}.research_sec_document_archive_time_v1 '
                f'WHERE document_key={literal(document["document_key"])} '
                f'AND policy_version={literal(sec_public_archive_time.VERSION)} AND mode={literal(mode)} LIMIT 2')
            if not rows:return None
            if len(rows)!=1:raise ValueError('SEC reuse has duplicate retained evidence')
            proof=json.loads(rows[0]['proof']);witness=proof['witness'];qualification=proof['qualification']
            if (witness['document_key']!=document['document_key'] or qualification['mode']!=mode
                    or witness['candidate_hash']!=lineage['candidate_hash']
                    or witness['document_hash']!=lineage['document']['content_sha256']
                    or witness['submission_hash']!=lineage['archive_submission_hash']):
                raise ValueError('SEC reused witness differs from current source lineage')
            witnesses.append(witness);qualified.append(qualification)
        # Exact normalized rows, receipt and complete group are independently reread.
        return self.publish(tuple(witnesses),tuple(qualified),receipt)

    def publish(self,witnesses,qualifications,receipt):
        groups=records(witnesses,qualifications,receipt);catalog=tables()
        metadata_bytes=sum(len(json.dumps(row,separators=(',',':')).encode()) for group in groups for row in group)
        if metadata_bytes>8*1024**2:raise ValueError('SEC witness group exceeds its metadata byte bound')
        def guard():
            self.claim.check();self.source_guard();self.claim.check()
            sec_share_capture.verify(self.query,self.database,required=True,catalog=catalog)
        def normal(row,table):
            row=dict(row)
            for c in table.columns:
                if c.type=='UInt64':row[c.name]=int(row[c.name])
            return row
        guard();inserted=0;reads=[]
        for table,expected in zip(catalog,groups):
            for row in expected:
                where=' AND '.join(f'{key}={literal(row[key])}' for key in table.order)
                def read():return [normal(r,table) for r in self.query(
                    f'SELECT {",".join(c.name for c in table.columns)} FROM {self.database}.{table.name} WHERE {where} LIMIT 2')]
                current=read()
                if current and current!=[row]:raise ValueError('SEC witness conflicts with immutable retained source evidence')
                if not current:
                    guard();admitted=self.admission(metadata_bytes)
                    if not isinstance(admitted,dict) or admitted.get('status')!='admitted' or admitted.get('metadata_bytes')!=metadata_bytes:
                        raise ValueError('SEC witness source-write admission differs')
                    guard();insert_rows(self.query,self.database,table.name,[row]);inserted+=1
                if read()!=[row]:raise ValueError('SEC witness publication readback differs')
                reads.append((table,where,row))
        guard()
        for table,where,row in reads:
            current=[normal(r,table) for r in self.query(
                f'SELECT {",".join(c.name for c in table.columns)} FROM {self.database}.{table.name} WHERE {where} LIMIT 2')]
            if current!=[row]:raise ValueError('SEC witness final group readback differs')
        return dict(persisted=True,readback_verified=True,inserted_rows=inserted,
            witness_hashes=tuple(w['witness_hash'] for w in witnesses),qualification_hashes=tuple(q['qualification_hash'] for q in qualifications),
            allocation_certified=False)

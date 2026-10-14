"""Compact reported-value source references with qualified document-time binding."""
import json
import re
from datetime import datetime,timezone
from . import schema,sec_precision,sec_public_archive_time,sec_share_capture,sec_fundamental_publication,sec_fundamental_readback
from .contracts import digest
from .publication import DictionaryClaim


def tables():
    c=schema.c;common=(c('projection_hash','FixedString(64)'),c('source_archive_date','Date'))
    return (
        schema.Table('research_sec_reported_group_v1',common+(c('context_hash','FixedString(64)'),c('extraction_hash','FixedString(64)'),
            c('source_position','UInt32','Delta,ZSTD(1)'),c('all_source_positions','Array(UInt32)'),
            c('lower_bound','String'),c('upper_bound','String'),c('lower_inclusive','UInt8'),c('upper_inclusive','UInt8')),
            ('projection_hash','context_hash'),'toYYYYMM(source_archive_date)'),
        schema.Table('research_sec_reported_document_v1',(c('binding_hash','FixedString(64)'),)+common+
            (c('extraction_hash','FixedString(64)'),c('source_reconciliation_hash','FixedString(64)'),c('document_key','FixedString(64)'),c('witness_hash','FixedString(64)'),
             c('qualification_hash','FixedString(64)'),c('numeric_mode','LowCardinality(String)'),c('gate_hash','FixedString(64)'),
             c('available_at',"DateTime64(6,'UTC')",'Delta,ZSTD(1)'),c('precision','UInt8'),c('groups','UInt32','T64,ZSTD(1)'),c('proof','String')),
            ('binding_hash',),'toYYYYMM(source_archive_date)'))


def records(extraction,witness,qualification,receipt,*,numeric_mode,source_reconciliation_hash):
    if not isinstance(source_reconciliation_hash,str) or not re.fullmatch('[0-9a-f]{64}',source_reconciliation_hash):
        raise ValueError('Reported projection requires a sealed source reconciliation locator')
    if (sec_public_archive_time.qualify(witness,receipt,mode=qualification['mode'])!=qualification
            or witness['document_hash']!=extraction['source_text_hash']):
        raise ValueError('Reported projection original document/time evidence differs')
    projection=sec_precision.select_reported(extraction,mode=numeric_mode)
    binding=dict(version='sec-reported-document-binding-v1',projection_hash=projection['projection_hash'],
        source_reconciliation_hash=source_reconciliation_hash,
        witness_hash=witness['witness_hash'],qualification_hash=qualification['qualification_hash'],document_key=witness['document_key'])
    binding_hash=digest(binding);common=dict(projection_hash=projection['projection_hash'],source_archive_date=witness['source_archive_date'])
    rows=tuple(dict(common,context_hash=row['context_hash'],extraction_hash=extraction['extraction_hash'],source_position=row['source_position'],
        all_source_positions=row['all_source_positions'],lower_bound=row['interval']['lower'],upper_bound=row['interval']['upper'],
        lower_inclusive=int(row['interval']['lower_inclusive']),upper_inclusive=int(row['interval']['upper_inclusive'])) for row in projection['groups'])
    header=dict(common,binding_hash=binding_hash,extraction_hash=extraction['extraction_hash'],source_reconciliation_hash=source_reconciliation_hash,document_key=witness['document_key'],
        witness_hash=witness['witness_hash'],qualification_hash=qualification['qualification_hash'],numeric_mode=numeric_mode,gate_hash=projection['gate_hash'],
        available_at=datetime.fromisoformat(qualification['available_at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
        precision=qualification['precision'],groups=len(rows),proof=json.dumps(dict(binding=binding,projection_metadata={k:v for k,v in projection.items() if k!='groups'}),
            sort_keys=True,separators=(',',':')))
    return rows,(header,)


def install(query,database,claim):
    if not isinstance(claim,DictionaryClaim) or claim.dictionary_database!=database:raise ValueError('Reported schema requires dictionary ownership')
    claim.check();sec_share_capture.verify(query,database,catalog=tables())
    for table in tables():claim.check();query(table.ddl(database),read=False)
    sec_share_capture.verify(query,database,required=True,catalog=tables());claim.check()


def capture(query,database,extraction,witness,qualification,receipt,claim,source_guard,admission,*,numeric_mode,source_reconciliation_hash):
    def verify_source():
        source_guard()
        verified=sec_fundamental_readback.verify(query,database,source_reconciliation_hash,claim,source_guard)
        if verified['extraction_hash']!=extraction['extraction_hash']:raise ValueError('Reported projection source references differ from verified original bank')
    verify_source()
    groups=records(extraction,witness,qualification,receipt,numeric_mode=numeric_mode,source_reconciliation_hash=source_reconciliation_hash);catalog=tables()
    scopes={catalog[0].name:('projection_hash',groups[-1][0]['projection_hash']),catalog[1].name:('binding_hash',groups[-1][0]['binding_hash'])}
    result=sec_fundamental_publication.capture_groups(query,database,catalog,groups,scopes,claim,source_guard,admission)
    verify_source()
    return dict(result,binding_hash=groups[-1][0]['binding_hash'],projection_hash=groups[-1][0]['projection_hash'],
        certified=False,allocation_certified=False)

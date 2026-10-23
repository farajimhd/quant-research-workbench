"""Verified subsidiary disclosures, separate from issuer identity and totals.

A disclosed name is not a resolved CIK. Exhibits may omit subsidiaries, and a
snapshot date is not an acquisition date or proof of continuing ownership.
"""
from hashlib import sha256
from datetime import date,datetime,timezone
import json,re
from html.parser import HTMLParser
from pipelines.certified_research import schema,sec_public_archive_time
from pipelines.certified_research.contracts import digest,literal,identifier
from pipelines.certified_research.publication import insert_rows
from .eligibility import require_eligible

VERSION='verified-subsidiary-disclosure-v1'


def table():
    c=schema.c
    return schema.Table('reference_family_disclosure_v1',(
        c('parent_cik','String'),c('available_at',"DateTime64(6,'UTC')"),
        c('assertion_id','FixedString(64)'),c('child_name','String'),c('child_cik','Nullable(String)'),
        c('identity_status','LowCardinality(String)'),c('relationship','LowCardinality(String)'),
        c('asserted_as_of','Date'),c('document_hash','FixedString(64)'),c('accession','String'),
        c('document','String'),c('observed_at',"DateTime64(6,'UTC')"),c('proof','String')),
        ('parent_cik','available_at','assertion_id'),'toYear(available_at)')


def verify(assertions,payload,witness,receipt,lineage):
    if not 1<=len(assertions)<=1000:raise ValueError('Family assertion scope exceeds bound')
    if len(payload.encode('utf-8'))>16*1024**2:raise ValueError('Family document exceeds bound')
    source_hash=sha256(payload.encode('utf-8')).hexdigest()
    if source_hash!=witness['document_hash']:raise ValueError('Family payload differs from archive witness')
    qualified=sec_public_archive_time.qualify(witness,receipt,mode='official_filing_day')
    if witness['candidate_hash']!=lineage['candidate_hash']:raise ValueError('Family witness lineage differs')
    doc=lineage['document']
    class Text(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
        def handle_data(self,data):self.parts.append(data)
    parser=Text();parser.feed(payload);parser.close()
    text=' '.join(' '.join(parser.parts).split()).casefold()
    rows=[];seen=set()
    for assertion in assertions:
        parent=assertion['parent_cik'];require_eligible('issuer_cik',[parent])
        reviewed_hash=assertion.get('source_sha256')
        if (not re.fullmatch('[0-9]{10}',parent) or reviewed_hash not in (source_hash,witness.get('retained_document_hash'))
                or int(parent)!=int(doc['cik']) or assertion.get('source_accession')!=doc['accession_number']
                or assertion.get('source_document')!=doc['document_name']
                or assertion.get('document_assertion_reviewed') is not True
                or assertion.get('relationship')!='disclosed_subsidiary_direct_or_indirect'
                or assertion.get('ownership_percent') is not None or assertion.get('complete_family') is not False
                or assertion.get('valid_from') is not None or assertion.get('valid_to') is not None):
            raise ValueError('Family review exceeds its documented assertion scope')
        name=assertion['child_name'];normalized=' '.join(name.split()).casefold()
        if not 1<=len(name)<=512 or normalized not in text:raise ValueError('Reviewed subsidiary is absent from exact document')
        if normalized in seen:raise ValueError('Duplicate subsidiary in reviewed disclosure')
        seen.add(normalized)
        asof=date.fromisoformat(assertion['asserted_as_of'])
        if asof>date.fromisoformat(witness['source_archive_date']):raise ValueError('Family snapshot chronology differs')
        # Identity candidates are deliberately not promoted by string matching.
        body=dict(version=VERSION,parent_cik=parent,child_name=name,child_cik=None,
            identity_status='unresolved',relationship=assertion['relationship'],asserted_as_of=str(asof),
            document_hash=source_hash,accession=assertion['source_accession'],document=assertion['source_document'],
            reviewed_document_hash=reviewed_hash,
            available_at=qualified['available_at'],qualification_hash=qualified['qualification_hash'],
            witness_hash=witness['witness_hash'],complete_family=False,ownership_interval_known=False,aggregation='none')
        rows.append(dict(body,assertion_id=digest(body)))
    result=dict(version=VERSION,rows=rows,verified=True,public_time_qualified=True,
        resolved_child_identities=0,unresolved_child_identities=len(rows),completeness='unknown',aggregation='none')
    return dict(result,verification_hash=digest(result))


def publish(query,database,verified,claim,guard,observed_at):
    body=dict(verified);seal=body.pop('verification_hash',None)
    if verified.get('verified') is not True or seal!=digest(body):raise ValueError('Unverified family disclosures')
    if observed_at.tzinfo is None:raise ValueError('Family observation timestamp must be aware')
    claim.check();guard()
    verify_storage(query,database)
    query(table().ddl(database),read=False)
    verify_storage(query,database,required=True)
    stamp=observed_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    rows=[]
    for source in verified['rows']:
        row={k:source[k] for k in ('parent_cik','assertion_id','child_name','child_cik','identity_status','relationship',
            'asserted_as_of','document_hash','accession','document')}
        row.update(available_at=datetime.fromisoformat(source['available_at']).strftime('%Y-%m-%d %H:%M:%S.%f'),
            observed_at=stamp,proof=json.dumps(source,sort_keys=True,separators=(',',':')))
        rows.append(row)
    ids=','.join(literal(r['assertion_id']) for r in rows)
    sql=f'SELECT * FROM {database}.{table().name} WHERE assertion_id IN ({ids}) LIMIT 1001'
    current=query(sql);by_id={r['assertion_id']:r for r in current}
    if len(by_id)!=len(current):raise ValueError('Duplicate immutable family assertions')
    for row in rows:
        prior=by_id.get(row['assertion_id'])
        if prior:
            row['observed_at']=prior['observed_at']
            if row!=prior:raise ValueError('Existing family evidence differs')
    missing=[r for r in rows if r['assertion_id'] not in by_id]
    for offset in range(0,len(missing),256):
        claim.check();guard();insert_rows(query,database,table().name,missing[offset:offset+256])
    actual=query(sql);claim.check();guard()
    if sorted(actual,key=lambda r:r['assertion_id'])!=sorted(rows,key=lambda r:r['assertion_id']):
        raise ValueError('Family disclosure readback differs')
    return dict(verified=True,published=len(missing),reused=len(rows)-len(missing),rows=len(rows),
        unresolved_child_identities=len(rows),completeness='unknown',aggregation='none')


def verify_storage(query,database,*,required=False):
    identifier(database);spec=table()
    policy=query("SELECT arrayJoin(disks) disk FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if not policy or any(r['disk']!='live_market_ssd' for r in policy):raise ValueError('Family SSD policy unavailable')
    where=f'database={literal(database)} AND name={literal(spec.name)}'
    tables=query('SELECT engine,sorting_key,partition_key,storage_policy FROM system.tables WHERE '+where)
    compact=lambda s:s.replace(' ','').replace('`','')
    if required and len(tables)!=1:raise ValueError('Family schema missing')
    if tables:
        row=tables[0]
        if (len(tables)!=1 or row['engine']!='MergeTree' or row['storage_policy']!='live_market_ssd'
                or compact(row['sorting_key'])!=','.join(spec.order) or compact(row['partition_key'])!=spec.partition):
            raise ValueError('Family table layout differs')
        columns=query(f'SELECT name,type FROM system.columns WHERE database={literal(database)} AND table={literal(spec.name)} ORDER BY position')
        if [(r['name'],compact(r['type'])) for r in columns]!=[(c.name,compact(c.type)) for c in spec.columns]:
            raise ValueError('Family table columns differ')
    parts=query(f'SELECT DISTINCT disk_name FROM system.parts WHERE active AND database={literal(database)} AND table={literal(spec.name)}')
    if any(r['disk_name']!='live_market_ssd' for r in parts):raise ValueError('Family parts are outside required SSD')

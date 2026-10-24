"""Explicit co-registrant contexts, never parent/subsidiary value aggregation.

The filing's DEI CIK declarations identify legal-entity members. Associations
reference an already qualified source; numeric facts remain stored exactly once.
"""
from hashlib import sha256
from io import BytesIO
import json,re
from xml.etree import ElementTree as ET
from pipelines.certified_research.contracts import digest,literal,identifier
from .eligibility import require_eligible

VERSION='qualified-joint-issuer-context-v1'
TABLE='reference_joint_issuer_v1'
COLUMNS=(('association_id','FixedString(64)'),('issuer_cik','String'),('source_cik','String'),
    ('source_id','FixedString(64)'),('document_hash','FixedString(64)'),('axis','String'),
    ('member','String'),('proof','String'))


def extract(payload,expected_hash,source_cik):
    """Bounded exact XML parsing of explicit DEI declarations only."""
    raw=payload.encode('utf-8');require_eligible('issuer_cik',[source_cik])
    if len(raw)>16*1024**2 or sha256(raw).hexdigest()!=expected_hash or b'<!ENTITY' in raw.upper():
        raise ValueError('Joint issuer original bytes differ or exceed bounds')
    raw=raw.strip()
    if raw.startswith(b'<XBRL>'):
        if not raw.endswith(b'</XBRL>'):raise ValueError('Incomplete joint issuer envelope')
        raw=raw[6:-7].strip()
    maps={};stack=[];pending=[];current={};nodes=0
    parser=ET.iterparse(BytesIO(raw),events=('start-ns','start','end'))
    for event,node in parser:
        if event=='start-ns':pending.append(node);continue
        if event=='start':
            current=dict(current,**dict(pending));pending=[];stack.append(current);maps[id(node)]=current;nodes+=1
            if nodes>500000:raise ValueError('Joint issuer XML node bound exceeded')
        else:
            stack.pop();current=stack[-1] if stack else {}
    root=parser.root
    def qname(text,node):
        pieces=text.split(':');ns=maps[id(node)]
        if len(pieces)!=2 or pieces[0] not in ns:raise ValueError('Unresolved joint issuer QName')
        return '{'+ns[pieces[0]]+'}'+pieces[1]
    def dei(value,local):
        return bool(re.fullmatch(r'\{https?://xbrl\.sec\.gov/dei/[0-9]{4}\}'+local,value))
    contexts={}
    for ctx in root.findall('.//{http://www.xbrl.org/2003/instance}context'):
        key=ctx.get('id');ids=ctx.findall('.//{http://www.xbrl.org/2003/instance}identifier')
        if not key or key in contexts:raise ValueError('Duplicate joint issuer context')
        dims=ctx.findall('.//{http://xbrl.org/2006/xbrldi}explicitMember')
        if (len(ids)!=1 or ids[0].get('scheme')!='http://www.sec.gov/CIK'
                or (ids[0].text or '').lstrip('0')!=source_cik.lstrip('0')):
            raise ValueError('Joint issuer context source differs')
        contexts[key]=None
        if len(dims)==1 and not ctx.findall('.//{http://xbrl.org/2006/xbrldi}typedMember'):
            axis=qname(dims[0].get('dimension',''),dims[0]);member=qname(dims[0].text or '',dims[0])
            if dei(axis,'LegalEntityAxis'):contexts[key]=(axis,member)
    declarations={}
    for node in root.iter():
        if node.tag not in ('{http://www.xbrl.org/2013/inlineXBRL}nonNumeric','{http://www.xbrl.org/2008/inlineXBRL}nonNumeric'):continue
        concept=qname(node.get('name',''),node)
        if not dei(concept,'EntityCentralIndexKey'):continue
        dim=contexts.get(node.get('contextRef'))
        if dim is None:continue
        if node.get('continuedAt') or node.get('format'):raise ValueError('Transformed joint CIK is unsupported')
        value=''.join(node.itertext()).strip()
        if not re.fullmatch('[0-9]{1,10}',value) or int(value)==0:raise ValueError('Invalid explicit joint issuer CIK')
        value=value.zfill(10);require_eligible('issuer_cik',[value])
        if dim in declarations and declarations[dim]!=value:raise ValueError('Conflicting explicit joint issuer CIK')
        declarations[dim]=value
    if not 1<=len(declarations)<=256:raise ValueError('No bounded explicit joint issuer declarations')
    return [dict(axis=k[0],member=k[1],issuer_cik=v) for k,v in sorted(declarations.items()) if v!=source_cik]


def install(query,database='arte'):
    identifier(database)
    policy=query("SELECT arrayJoin(disks) disk FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if not policy or any(x['disk']!='live_market_ssd' for x in policy):raise ValueError('Joint issuer SSD policy unavailable')
    verify(query,database,required=False)
    query(f'CREATE TABLE IF NOT EXISTS {database}.{TABLE} ('+','.join(n+' '+t for n,t in COLUMNS)+
        ") ENGINE=MergeTree ORDER BY (issuer_cik,source_id,association_id) SETTINGS storage_policy='live_market_ssd'",read=False)
    verify(query,database)


def verify(query,database='arte',*,required=True):
    identifier(database);where=f'database={literal(database)} AND name={literal(TABLE)}'
    tables=query('SELECT engine,sorting_key,partition_key,storage_policy FROM system.tables WHERE '+where)
    if required and len(tables)!=1:raise ValueError('Joint issuer table missing')
    compact=lambda s:s.replace(' ','').replace('`','')
    if tables:
        t=tables[0]
        if len(tables)!=1 or t['engine']!='MergeTree' or compact(t['sorting_key'])!='issuer_cik,source_id,association_id' or compact(t['partition_key'])!='tuple()' or t['storage_policy']!='live_market_ssd':raise ValueError('Joint issuer table layout differs')
        columns=query(f'SELECT name,type FROM system.columns WHERE database={literal(database)} AND table={literal(TABLE)} ORDER BY position')
        if [(x['name'],compact(x['type'])) for x in columns]!=[(n,compact(t)) for n,t in COLUMNS]:raise ValueError('Joint issuer columns differ')
    parts=query(f'SELECT DISTINCT disk_name FROM system.parts WHERE active AND database={literal(database)} AND table={literal(TABLE)}')
    if any(x['disk_name']!='live_market_ssd' for x in parts):raise ValueError('Joint issuer parts misplaced')


def publish(query,database,request,binding_hash,payload,claim,source_guard):
    from pipelines.certified_research.sec_original_authority import verify_document
    from pipelines.certified_research.publication import insert_rows
    authority=verify_document(query,'q_live',*request,binding_hash,claim,source_guard)
    if authority.get('document_qualified') is not True:raise ValueError('Joint issuer source must be document-qualified')
    source_id=authority['authority_hash'];source_cik=authority['cik'];rows=[]
    sources=query(f"SELECT source_id FROM {database}.reference_source_v1 FINAL WHERE source_id={literal(source_id)} AND family='sec_original_qualified' AND quality='certified' AND entity_id={literal(source_cik)}")
    if len(sources)!=1:raise ValueError('Qualified normalized source is missing')
    filers=query('SELECT DISTINCT entity_cik FROM q_live.sec_filing_entity_v3 WHERE accession_number='+literal(request[1])+" AND entity_role='filer'")
    allowed={x['entity_cik'] for x in filers}
    for association in extract(payload,authority['document_hash'],source_cik):
        if association['issuer_cik'] not in allowed:raise ValueError('Explicit joint issuer lacks co-registrant evidence')
        proof=dict(version=VERSION,accession=request[1],document=request[2],source_id=source_id,
            source_cik=source_cik,document_hash=authority['document_hash'],**association)
        rows.append(dict(association_id=digest(proof),source_id=source_id,source_cik=source_cik,
            document_hash=authority['document_hash'],**association,proof=json.dumps(proof,sort_keys=True,separators=(',',':'))))
    install(query,database);claim.check();source_guard()
    sql=f'SELECT * FROM {database}.{TABLE} WHERE source_id={literal(source_id)}'
    current=query(sql);prior={x['association_id']:x for x in current}
    if len(prior)!=len(current) or any(x not in rows for x in current):raise ValueError('Joint issuer stored evidence differs')
    missing=[x for x in rows if x['association_id'] not in prior]
    if missing:insert_rows(query,database,TABLE,missing)
    actual=query(sql);claim.check();source_guard();verify(query,database)
    if sorted(actual,key=lambda x:x['association_id'])!=sorted(rows,key=lambda x:x['association_id']):raise ValueError('Joint issuer independent readback differs')
    return dict(rows=len(rows),published=len(missing),reused=len(rows)-len(missing),source_id=source_id)


def base_sql(ciks,database,known_at=None):
    """Resolve only a sole legal-entity dimension; keep all other contexts out."""
    from .v1 import field_cases
    from .queries import _timestamp
    identifier(database);require_eligible('issuer_cik',ciks)
    if not 1<=len(ciks)<=128:raise ValueError('Bound joint issuer query to 128 CIKs')
    ids=','.join(map(literal,sorted(set(ciks))))
    links=f'SELECT DISTINCT issuer_cik,source_cik,source_id,axis,member FROM {database}.{TABLE} WHERE issuer_cik IN ({ids})'
    known='' if known_at is None else ' AND observed_at<='+_timestamp(known_at)
    tag="arrayElement(splitByChar(':',f.metric),-1)";selected=field_cases(tag)
    facts=f'''SELECT * FROM {database}.reference_fact_v1 FINAL WHERE entity_kind='issuer_cik'
      AND entity_id IN (SELECT source_cik FROM ({links})) AND source_id IN (SELECT source_id FROM ({links}))
      AND ({selected.replace('f.metric','metric')})!='' '''
    return f'''SELECT a.issuer_cik cik,{selected} feature,f.metric source_metric,
      c.period_start,c.period_end,if(match(c.unit,'^[A-Z]{{3}}/share$'),concat(substring(c.unit,1,3),'/shares'),c.unit) unit,
      c.unit source_unit,'{{}}' dimensions,c.dimensions source_dimensions,s.source_id AS source_id,s.family,
      s.event_key accession,s.observed_at,coalesce(f.text_value,toString(f.value)) value,
      s.available_at public_at,1 authority_priority
      FROM ({facts}) f
      INNER JOIN ({links}) a ON f.source_id=a.source_id AND f.entity_id=a.source_cik
      INNER JOIN (SELECT * FROM {database}.reference_source_v1 FINAL WHERE family='sec_original_qualified'
        AND quality='certified' AND entity_id IN (SELECT source_cik FROM ({links}))
        AND source_id IN (SELECT source_id FROM ({links})){known}) s ON f.source_id=s.source_id
      INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL
        WHERE context_id IN (SELECT context_id FROM ({facts}))) c ON f.context_id=c.context_id
      WHERE c.taxonomy IN ('us-gaap','ifrs-full','dei') AND f.metric=concat(c.taxonomy,':',{tag})
        AND JSONExtract(c.dimensions,'Array(Array(String))')=[[a.axis,a.member]]
        AND isNotNull(c.period_end) AND isNotNull(value)'''

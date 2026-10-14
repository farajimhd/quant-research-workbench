"""Lossless inline-XBRL share observations, not allocation certificates."""
from datetime import date
from decimal import Decimal,InvalidOperation
from hashlib import sha256
from io import BytesIO
import re
from xml.etree import ElementTree as ET

VERSION='sec-inline-share-context-v1'
XBRL='http://www.xbrl.org/2003/instance'
DIM='http://xbrl.org/2006/xbrldi'
IX='http://www.xbrl.org/2013/inlineXBRL'
TAGS={'EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding'}


def extract(payload,expected_hash,cik,*,maximum_bytes=16*1024**2,maximum_facts=10000):
    """Retain distinct facts and dimensions; never infer class completeness.

    Inputs are exact UTF-8 source text and its independently retained text hash.
    The caller must separately qualify original document vintages/availability.
    No HTML recovery, entity expansion, network access or numeric rounding.
    """
    if (not isinstance(payload,str) or not isinstance(expected_hash,str) or not re.fullmatch('[0-9a-f]{64}',expected_hash)
            or not isinstance(cik,str) or not re.fullmatch('0*[1-9][0-9]{0,9}',cik)
            or type(maximum_bytes) is not int or not 1<=maximum_bytes<=64*1024**2
            or type(maximum_facts) is not int or not 1<=maximum_facts<=10000):
        raise ValueError('Invalid bounded SEC context extraction')
    if len(payload)>maximum_bytes:raise ValueError('SEC source exceeds extraction byte bound')
    raw=payload.encode('utf-8')
    if len(raw)>maximum_bytes:raise ValueError('SEC source exceeds extraction byte bound')
    if sha256(raw).hexdigest()!=expected_hash:raise ValueError('SEC source text hash differs')
    if b'<!ENTITY' in raw.upper():raise ValueError('Entity declarations are unsupported')
    parsing_view=raw.lstrip(b' \t\r\n')
    prefix_bytes=len(raw)-len(parsing_view);suffix_bytes=0
    envelope=parsing_view.startswith(b'<XBRL>')
    if envelope:
        trimmed=parsing_view.rstrip(b' \t\r\n')
        if not trimmed.endswith(b'</XBRL>'):raise ValueError('SEC XBRL envelope is incomplete')
        suffix_bytes=len(parsing_view)-len(trimmed)+len(b'</XBRL>')
        inner=trimmed[len(b'<XBRL>'):-len(b'</XBRL>')]
        parsing_view=inner.lstrip(b' \t\r\n')
        prefix_bytes+=len(b'<XBRL>')+len(inner)-len(parsing_view)
    namespaces={};stack=[];pending=[];maps={};nodes=0
    try:
        parser=ET.iterparse(BytesIO(parsing_view),events=('start-ns','start','end'))
        for event,node in parser:
            if event=='start-ns':pending.append(node);continue
            if event=='start':
                if pending:namespaces=dict(namespaces,**dict(pending));pending=[]
                stack.append(namespaces);nodes+=1
                if nodes>250000 or len(stack)>128:raise ValueError('SEC XML complexity exceeds its bound')
                if node.tag.rsplit('}',1)[-1]=='nonFraction' or node.tag in ('{'+XBRL+'}measure','{'+DIM+'}explicitMember'):
                    maps[id(node)]=namespaces
            else:
                stack.pop();namespaces=stack[-1] if stack else {}
        root=parser.root
    except ET.ParseError as error:raise ValueError('SEC source is not strict XML') from error
    def qname(value,node):
        prefix,sep,local=value.partition(':')
        if not sep:local=prefix;prefix=''
        uri=maps[id(node)].get(prefix)
        if not uri or not local:raise ValueError('Unresolved XBRL QName')
        return '{'+uri+'}'+local
    contexts={};units={}
    for tag,destination in (('context',contexts),('unit',units)):
        for node in root.iter('{'+XBRL+'}'+tag):
            key=node.get('id')
            if not key or key in destination:raise ValueError('Missing or duplicate XBRL identity')
            destination[key]=node
    observations=[];rejected=[]
    for position,fact in enumerate(n for n in root.iter() if n.tag.rsplit('}',1)[-1]=='nonFraction'):
        name=fact.get('name','');local=name.split(':')[-1]
        if local not in TAGS:continue
        if len(observations)+len(rejected)>=maximum_facts:raise ValueError('SEC share fact bound exceeded')
        row=dict(source_position=position,fact_id=fact.get('id',''),context_id=fact.get('contextRef',''),concept=name,
            unit_id=fact.get('unitRef',''),decimals=fact.get('decimals',''),scale=fact.get('scale','0'),
            format=fact.get('format',''),source_text_hash=expected_hash)
        try:
            if fact.tag!='{'+IX+'}nonFraction':raise ValueError('unsupported_inline_namespace')
            concept=qname(name,fact)
            if not re.fullmatch(r'\{http://xbrl.sec.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?\}EntityCommonStockSharesOutstanding|\{http://fasb.org/us-gaap/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?\}CommonStockSharesOutstanding',concept):
                raise ValueError('unsupported_share_taxonomy')
            context=contexts.get(row['context_id']);unit=units.get(row['unit_id'])
            if context is None or unit is None:raise ValueError('missing_context_or_unit')
            ids=context.findall('.//{'+XBRL+'}identifier')
            if len(ids)!=1 or ids[0].get('scheme')!='http://www.sec.gov/CIK' or str(int(ids[0].text or '0'))!=str(int(cik)):
                raise ValueError('issuer_context_mismatch')
            instant=context.findall('./{'+XBRL+'}period/{'+XBRL+'}instant')
            if len(instant)!=1:raise ValueError('share_context_requires_instant')
            measured=date.fromisoformat(instant[0].text)
            measures=unit.findall('./{'+XBRL+'}measure')
            if len(measures)!=1 or qname(measures[0].text,measures[0])!='{'+XBRL+'}shares':raise ValueError('unsupported_share_unit')
            if any(True for _ in context.iter('{'+DIM+'}typedMember')):raise ValueError('unsupported_typed_dimension')
            dimensions=tuple(sorted((qname(m.get('dimension',''),m),qname((m.text or '').strip(),m))
                for m in context.iter('{'+DIM+'}explicitMember')))
            if len({axis for axis,member in dimensions})!=len(dimensions):raise ValueError('duplicate_dimension_axis')
            if fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1'):raise ValueError('nil_share_fact')
            if any(True for _ in fact.iter('{'+IX+'}exclude')):raise ValueError('unsupported_inline_exclusion')
            if row['format'] and row['format'].split(':')[-1] not in ('num-dot-decimal','numdotdecimal'):
                raise ValueError('unsupported_numeric_transform')
            if row['format'] and not re.fullmatch(r'\{http://www.xbrl.org/inlineXBRL/transformation/[0-9]{4}-[0-9]{2}-[0-9]{2}\}.+',qname(row['format'],fact)):
                raise ValueError('unsupported_transform_namespace')
            text=''.join(fact.itertext()).strip()
            if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',text):raise ValueError('invalid_share_number')
            scale=int(row['scale'])
            if not -12<=scale<=12 or fact.get('sign','') not in ('','+'):raise ValueError('invalid_share_scale_or_sign')
            number=Decimal(text.replace(',','')).as_tuple()
            value=Decimal((number.sign,number.digits,number.exponent+scale))
            observations.append(dict(row,concept=concept,period_end=str(measured),dimensions=dimensions,value=format(value,'f')))
        except (ValueError,TypeError,InvalidOperation) as error:
            rejected.append(dict(row,reason=str(error)))
    return dict(version=VERSION,observations=tuple(observations),rejected=tuple(rejected),
        source_text_hash=expected_hash,parsing_prefix_bytes=prefix_bytes,parsing_suffix_bytes=suffix_bytes,
        sec_xbrl_envelope=envelope,allocation_certified=False)

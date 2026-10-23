"""Lossless inline-XBRL share observations, not allocation certificates."""
from datetime import date
from decimal import Decimal,InvalidOperation
from hashlib import sha256
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from . import sec_taxonomy

VERSION='sec-inline-share-context-v1'
XBRL='http://www.xbrl.org/2003/instance'
DIM='http://xbrl.org/2006/xbrldi'
IX='http://www.xbrl.org/2013/inlineXBRL'
TAGS={'EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding'}


def extract(payload,expected_hash,cik,*,maximum_bytes=16*1024**2,maximum_facts=10000,catalog_tags=None,include_security_cover=False):
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
    elements=[];retention=[];retained_nodes=0
    collected={'context':[], 'unit':[], 'nonFraction':[], 'nonNumeric':[]}
    try:
        parser=ET.iterparse(BytesIO(parsing_view),events=('start-ns','start','end'))
        for event,node in parser:
            if event=='start-ns':pending.append(node);continue
            if event=='start':
                if pending:namespaces=dict(namespaces,**dict(pending));pending=[]
                stack.append(namespaces);nodes+=1
                local=node.tag.rsplit('}',1)[-1]
                selected=(local in ('nonFraction','nonNumeric') or node.tag in ('{'+XBRL+'}context','{'+XBRL+'}unit'))
                keep=selected or bool(retention and retention[-1])
                retention.append(keep);elements.append(node)
                if keep:retained_nodes+=1
                # Presentation markup can dominate large annual reports. Bound
                # total work separately from retained evidence/tree memory.
                if nodes>2000000 or retained_nodes>250000 or len(stack)>128:
                    raise ValueError('SEC XML complexity exceeds its bound')
                if selected:collected[local].append(node)
                if local in ('nonFraction','nonNumeric') or node.tag in ('{'+XBRL+'}measure','{'+DIM+'}explicitMember'):
                    maps[id(node)]=namespaces
            else:
                keep=retention.pop();elements.pop()
                # Preserve text, tails, nested facts and every context subtree.
                # Outside evidence subtrees discard presentation immediately.
                if not keep:
                    node.clear()
                    if elements:elements[-1].remove(node)
                stack.pop();namespaces=stack[-1] if stack else {}
    except ET.ParseError as error:raise ValueError('SEC source is not strict XML') from error
    def qname(value,node):
        prefix,sep,local=value.partition(':')
        if not sep:local=prefix;prefix=''
        uri=maps[id(node)].get(prefix)
        if not uri or not local:raise ValueError('Unresolved XBRL QName')
        return '{'+uri+'}'+local
    contexts={};units={}
    for tag,destination in (('context',contexts),('unit',units)):
        for node in collected[tag]:
            key=node.get('id')
            if not key or key in destination:raise ValueError('Missing or duplicate XBRL identity')
            destination[key]=node
    fundamental=catalog_tags is not None
    if fundamental and (not isinstance(catalog_tags,(tuple,list)) or not catalog_tags or len(catalog_tags)>1000
            or any(not isinstance(tag,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',tag) for tag in catalog_tags)):
        raise ValueError('Invalid bounded fundamental concept catalog')
    selected_tags=set(catalog_tags) if fundamental else TAGS
    observations=[];rejected=[]
    for position,fact in enumerate(collected['nonFraction']):
        name=fact.get('name','');local=name.split(':')[-1]
        if local not in selected_tags:continue
        if len(observations)+len(rejected)>=maximum_facts:raise ValueError('SEC share fact bound exceeded')
        row=dict(source_position=position,fact_id=fact.get('id',''),context_id=fact.get('contextRef',''),concept=name,
            unit_id=fact.get('unitRef',''),decimals=fact.get('decimals',''),scale=fact.get('scale','0'),
            format=fact.get('format',''),source_text_hash=expected_hash)
        try:
            if fact.tag!='{'+IX+'}nonFraction':raise ValueError('unsupported_inline_namespace')
            concept=qname(name,fact)
            taxonomy_pattern=(r'\{(?:http://xbrl.sec.gov/dei|http://fasb.org/us-gaap)/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?\}[A-Za-z_][A-Za-z0-9_]*'
                if fundamental else r'\{http://xbrl.sec.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?\}EntityCommonStockSharesOutstanding|\{http://fasb.org/us-gaap/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?\}CommonStockSharesOutstanding')
            if fundamental:
                try:sec_taxonomy.identity(concept)
                except ValueError:raise ValueError('unsupported_share_taxonomy') from None
            elif not re.fullmatch(taxonomy_pattern,concept):
                raise ValueError('unsupported_share_taxonomy')
            context=contexts.get(row['context_id']);unit=units.get(row['unit_id'])
            if context is None or unit is None:raise ValueError('missing_context_or_unit')
            ids=context.findall('.//{'+XBRL+'}identifier')
            if len(ids)!=1 or ids[0].get('scheme')!='http://www.sec.gov/CIK' or str(int(ids[0].text or '0'))!=str(int(cik)):
                raise ValueError('issuer_context_mismatch')
            instant=context.findall('./{'+XBRL+'}period/{'+XBRL+'}instant')
            starts=context.findall('./{'+XBRL+'}period/{'+XBRL+'}startDate')
            ends=context.findall('./{'+XBRL+'}period/{'+XBRL+'}endDate')
            period_start=None
            if len(instant)==1 and not starts and not ends:measured=date.fromisoformat(instant[0].text)
            elif fundamental and not instant and len(starts)==len(ends)==1:
                period_start=date.fromisoformat(starts[0].text);measured=date.fromisoformat(ends[0].text)
                if period_start>measured:raise ValueError('invalid_duration_order')
            else:raise ValueError('share_context_requires_instant' if not fundamental else 'invalid_fundamental_period')
            measures=unit.findall('./{'+XBRL+'}measure')
            unit_code=None
            if len(measures)==1 and len(list(unit))==1:
                measure=qname(measures[0].text,measures[0])
                if measure=='{'+XBRL+'}shares':unit_code='shares'
                elif fundamental and measure=='{'+XBRL+'}pure':unit_code='ratio'
                elif fundamental and re.fullmatch(r'\{http://www.xbrl.org/2003/iso4217\}[A-Z]{3}',measure):unit_code=measure[-3:]
            elif fundamental and len(list(unit))==1:
                divide=unit.find('./{'+XBRL+'}divide')
                if divide is not None and len(list(divide))==2:
                    numerator=divide.findall('./{'+XBRL+'}unitNumerator/{'+XBRL+'}measure')
                    denominator=divide.findall('./{'+XBRL+'}unitDenominator/{'+XBRL+'}measure')
                    if len(numerator)==len(denominator)==1:
                        currency=qname(numerator[0].text,numerator[0])
                        if (re.fullmatch(r'\{http://www.xbrl.org/2003/iso4217\}[A-Z]{3}',currency)
                                and qname(denominator[0].text,denominator[0])=='{'+XBRL+'}shares'):
                            unit_code=currency[-3:]+'/share'
            if unit_code is None:raise ValueError('unsupported_share_unit' if not fundamental else 'unsupported_fundamental_unit')
            if any(True for _ in context.iter('{'+DIM+'}typedMember')):raise ValueError('unsupported_typed_dimension')
            dimensions=tuple(sorted((qname(m.get('dimension',''),m),qname((m.text or '').strip(),m))
                for m in context.iter('{'+DIM+'}explicitMember')))
            if len({axis for axis,member in dimensions})!=len(dimensions):raise ValueError('duplicate_dimension_axis')
            if fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1'):
                if ''.join(fact.itertext()).strip():raise ValueError('nil_fact_has_content')
                raise ValueError('nil_share_fact')
            if any(True for _ in fact.iter('{'+IX+'}exclude')):raise ValueError('unsupported_inline_exclusion')
            fixed_zero=fundamental and row['format'].split(':')[-1]=='fixed-zero'
            word_number=fundamental and row['format'].split(':')[-1]=='numwordsen'
            if row['format'] and row['format'].split(':')[-1] not in ('num-dot-decimal','numdotdecimal') and not (fixed_zero or word_number):
                raise ValueError('unsupported_numeric_transform')
            if row['format'] and not word_number and not re.fullmatch(r'\{http://www.xbrl.org/inlineXBRL/transformation/[0-9]{4}-[0-9]{2}-[0-9]{2}\}.+',qname(row['format'],fact)):
                raise ValueError('unsupported_transform_namespace')
            text=''.join(fact.itertext()).strip()
            raw_text=text
            if word_number:
                if qname(row['format'],fact)!='{http://www.sec.gov/inlineXBRL/transformation/2015-08-31}numwordsen':
                    raise ValueError('unsupported_transform_namespace')
                # SEC EFM II 5.2.5.12 explicitly defines no/none as exact zero.
                # Other English-number inputs stay rejected until implemented
                # and validated; never guess from a partial word match.
                if text.casefold() not in ('no','none'):
                    raise ValueError('unsupported_numwordsen_input')
                text='0'
            if fixed_zero:
                if qname(row['format'],fact)!='{http://www.xbrl.org/inlineXBRL/transformation/2020-02-12}fixed-zero':
                    raise ValueError('unsupported_fixed_zero_namespace')
                text='0'
            if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',text):raise ValueError('invalid_share_number')
            scale=int(row['scale'])
            if not -12<=scale<=12 or fact.get('sign','') not in (('','+','-') if fundamental else ('','+')):raise ValueError('invalid_share_scale_or_sign')
            number=Decimal(text.replace(',','')).as_tuple()
            value=Decimal((int(fact.get('sign','')=='-'),number.digits,number.exponent+scale))
            observation=dict(row,concept=concept,period_end=str(measured),dimensions=dimensions,value=format(value,'f'))
            if fundamental:observation.update(period_start=str(period_start) if period_start else None,unit=unit_code,raw_text=raw_text)
            observations.append(observation)
        except (ValueError,TypeError,InvalidOperation) as error:
            rejected.append(dict(row,reason=str(error)))
    result=dict(version='sec-inline-fundamental-context-v4' if fundamental else VERSION,observations=tuple(observations),rejected=tuple(rejected),
        source_text_hash=expected_hash,parsing_prefix_bytes=prefix_bytes,parsing_suffix_bytes=suffix_bytes,
        sec_xbrl_envelope=envelope,allocation_certified=False)
    if include_security_cover:
        cover=[];failures=[];selected={'Security12bTitle','TradingSymbol','SecurityExchangeName'}
        for position,fact in enumerate(collected['nonNumeric']):
            name=fact.get('name','')
            if name.split(':')[-1] not in selected:continue
            if len(cover)+len(failures)>=10000:raise ValueError('SEC cover fact bound exceeded')
            row=dict(source_position=position,concept=name,context_id=fact.get('contextRef',''),fact_id=fact.get('id',''),format=fact.get('format',''))
            try:
                if fact.tag!='{'+IX+'}nonNumeric':raise ValueError('unsupported_cover_inline_namespace')
                concept=qname(name,fact)
                if not re.fullmatch(r'\{http://xbrl.sec.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?\}.+',concept):
                    raise ValueError('unsupported_cover_taxonomy')
                if fact.get('continuedAt') or fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1'):
                    raise ValueError('unsupported_cover_transform_continuation_or_nil')
                if any(True for _ in fact.iter('{'+IX+'}exclude')):raise ValueError('unsupported_cover_exclusion')
                context=contexts.get(row['context_id'])
                if context is None:raise ValueError('missing_cover_context')
                ids=context.findall('.//{'+XBRL+'}identifier')
                if len(ids)!=1 or ids[0].get('scheme')!='http://www.sec.gov/CIK' or str(int(ids[0].text or '0'))!=str(int(cik)):
                    raise ValueError('cover_issuer_context_mismatch')
                if any(True for _ in context.iter('{'+DIM+'}typedMember')):raise ValueError('unsupported_cover_typed_dimension')
                dimensions=tuple(sorted((qname(m.get('dimension',''),m),qname((m.text or '').strip(),m))
                    for m in context.iter('{'+DIM+'}explicitMember')))
                if len({axis for axis,member in dimensions})!=len(dimensions):raise ValueError('duplicate_cover_dimension_axis')
                instant=context.findall('./{'+XBRL+'}period/{'+XBRL+'}instant')
                starts=context.findall('./{'+XBRL+'}period/{'+XBRL+'}startDate')
                ends=context.findall('./{'+XBRL+'}period/{'+XBRL+'}endDate')
                if len(instant)==1 and not starts and not ends:
                    period_start=None;period_end=str(date.fromisoformat(instant[0].text))
                elif not instant and len(starts)==len(ends)==1:
                    period_start=str(date.fromisoformat(starts[0].text));period_end=str(date.fromisoformat(ends[0].text))
                    if period_start>period_end:raise ValueError('invalid_cover_period_order')
                else:raise ValueError('invalid_cover_period')
                text=''.join(fact.itertext()).strip()
                if not text or len(text)>4096:raise ValueError('empty_or_oversized_cover_value')
                transformed=text
                if row['format']:
                    if (name.split(':')[-1]!='SecurityExchangeName' or qname(row['format'],fact)!=
                            '{http://www.sec.gov/inlineXBRL/transformation/2015-08-31}exchnameen'):
                        raise ValueError('unsupported_cover_transform')
                    reviewed={'the nasdaq stock market llc':'NASDAQ','nasdaq stock market llc':'NASDAQ','nasdaq global select market':'NASDAQ','nasdaq':'NASDAQ',
                        'the new york stock exchange':'NYSE','new york stock exchange llc':'NYSE','nyse american llc':'NYSEAMER',
                        'nyse arca, inc.':'NYSEArca'}
                    transformed=reviewed.get(text.casefold())
                    if transformed is None:raise ValueError('unsupported_exchange_transform_input')
                cover.append(dict(row,concept=concept,dimensions=dimensions,value=text,transformed_value=transformed,
                    period_start=period_start,period_end=period_end))
            except (ValueError,TypeError) as error:failures.append(dict(row,reason=str(error)))
        result['security_cover']=dict(observations=tuple(cover),rejected=tuple(failures),class_completeness_proven=False)
    return result

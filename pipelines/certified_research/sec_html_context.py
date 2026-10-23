"""Source-bound extraction of reviewed untagged HTML cells.

HTML is parsed as HTML, never repaired into fictional XBRL. Semantic mappings
are explicit reviewed inputs bound to the entire original document. No guessed
currency, period, scale, taxonomy, dash-as-zero, or label fuzzy matching.
"""
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import re
from .contracts import digest
from . import facts, sec_taxonomy
from .sec_share_context import _period_date

VERSION='sec-reviewed-html-context-v1'


def normalize(text):
    return ' '.join(text.split())


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables=[];self.stack=[];self.text=[];self.events=0;self.hidden=0

    def handle_starttag(self,tag,attrs):
        self.events+=1
        if self.events>1000000:raise ValueError('HTML complexity exceeds bound')
        if tag in ('script','style'):self.hidden+=1
        if tag=='table':
            if len(self.tables)>=10000 or len(self.stack)>=32:raise ValueError('HTML table bound exceeded')
            row=dict(rows=[],nested=False,cell_open=False,preceding=normalize(' '.join(self.text[-256:]))[-2048:])
            if self.stack:self.stack[-1]['nested']=True
            self.tables.append(row);self.stack.append(row)
        elif tag=='tr' and self.stack:
            self.stack[-1]['cell_open']=False
            self.stack[-1]['rows'].append([])
        elif tag in ('td','th') and self.stack:
            if not self.stack[-1]['rows']:raise ValueError('HTML cell outside row')
            a=dict(attrs)
            self.stack[-1]['cell_open']=True
            self.stack[-1]['rows'][-1].append(dict(text='',rowspan=a.get('rowspan','1'),colspan=a.get('colspan','1')))
        elif tag=='br':self.handle_data(' ')

    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if tag=='table':
            if not self.stack:raise ValueError('Unbalanced HTML table')
            self.stack.pop()
        # Keep cell text strictly inside TD/TH, including inline markup.
        if tag in ('td','th','tr') and self.stack:self.stack[-1]['cell_open']=False

    def handle_data(self,text):
        if self.hidden:return
        self.text.append(text)
        if self.stack and self.stack[-1]['cell_open'] and self.stack[-1]['rows'] and self.stack[-1]['rows'][-1]:
            # Whitespace between cells cannot change the normalized value.
            self.stack[-1]['rows'][-1][-1]['text']+=text


def parse(payload,expected_hash,maximum_bytes=64*1024**2):
    if not isinstance(payload,str) or len(payload)>maximum_bytes:raise ValueError('HTML source exceeds bound')
    raw=payload.encode('utf-8')
    if len(raw)>maximum_bytes or sha256(raw).hexdigest()!=expected_hash:raise ValueError('HTML original source hash differs')
    if re.search(r'<!ENTITY|<\s*ix:',payload,re.I):raise ValueError('Tagged or entity-declaring source is not untagged HTML')
    parser=Tables();parser.feed(payload);parser.close()
    if parser.stack:raise ValueError('Unclosed HTML table')
    return parser


def extract(payload,expected_hash,cik,plan,*,maximum_bytes=64*1024**2):
    """A plan enumerates exact selected cells/quotes and explicit contexts.

    Scope is reported as selected observations, never complete-document or
    issuer coverage. A plan cannot turn an empty/ambiguous filing into zeros.
    """
    body=dict(plan);seal=body.pop('plan_hash',None)
    if (seal!=digest(body) or body.get('version')!=VERSION or body.get('document_hash')!=expected_hash
            or str(int(cik)).zfill(10)!=body.get('cik')
            or body.get('scope')!='reviewed_observations' or not 1<=len(body.get('facts',[]))<=10000):
        raise ValueError('HTML reviewed plan identity or scope differs')
    parsed=parse(payload,expected_hash,maximum_bytes);text=normalize(' '.join(parsed.text));observations=[];positions=set()
    catalog=facts.catalog();tags={t for metric in catalog for t in metric.tags}
    for position,item in enumerate(body['facts']):
        concept=item['concept'];taxonomy,tag=sec_taxonomy.identity(concept,allow_extension=True)
        native=None
        if taxonomy.startswith('html-native|'):
            from .sec_html_native import verify
            native=verify(item,cik)
        elif taxonomy.startswith('extension|') or tag not in tags or 'native' in item:
            raise ValueError('HTML concept is outside reviewed fundamental catalog')
        start=item['period_start'];end=str(_period_date(item['period_end']))
        if start is not None:
            start=str(_period_date(start))
            if start>end:raise ValueError('HTML period order differs')
        unit=item['unit'];scale=item['scale']
        if unit not in (*facts.CURRENCY_UNITS,'shares',*(u+'/shares' for u in facts.CURRENCY_UNITS)):
            raise ValueError('HTML unit is unsupported')
        if type(scale) is not int or not 0<=scale<=9:raise ValueError('HTML scale is unsupported')
        evidence=item['evidence']
        if not 1<=len(evidence)<=12 or any(not isinstance(q,str) or not q or len(q)>4096 or q not in text for q in evidence):
            raise ValueError('HTML period/unit/issuer review evidence differs')
        dimensions=tuple(tuple(d) for d in item.get('dimensions',()))
        if dimensions and (len(dimensions)!=1 or len(dimensions[0])!=2
                or dimensions[0][0]!='reviewed-html:share-class'
                or dimensions[0][1] not in ('Class A common stock','Class B common stock','Class C capital stock')
                or unit!='shares' or dimensions[0][1] not in ' '.join(evidence)):
            raise ValueError('HTML share-class context lacks reviewed source evidence')
        locator=item['locator']
        if locator['kind']=='cells':
            ti,ri,columns=locator['table'],locator['row'],locator['columns']
            if type(ti) is not int or type(ri) is not int or ti<0 or ri<0:raise ValueError('Invalid HTML locator')
            table=parsed.tables[ti]
            if table['nested']:raise ValueError('Nested layout table cannot be a financial fact')
            if 'table_proof' in item:
                proof=item['table_proof']
                context=table['preceding']+' '+normalize(' '.join(c['text'] for row in table['rows'][:10] for c in row))
                if not proof['heading'] or proof['heading'] not in context:
                    raise ValueError('HTML statement subject heading differs')
                for index,cells in proof['headers']:
                    if [normalize(c['text']) for c in table['rows'][index]]!=cells:
                        raise ValueError('HTML reviewed period column headers differ')
            row=table['rows'][ri]
            if (not isinstance(columns,list) or not 1<=len(columns)<=4 or columns!=sorted(set(columns))
                    or any(type(c) is not int or c<0 for c in columns)):raise ValueError('Invalid HTML cell selection')
            label=normalize(row[locator['label_column']]['text'])
            if label!=item['label']:raise ValueError('HTML reviewed row label differs')
            cells=[row[c] for c in columns]
            if any(c['rowspan']!='1' for c in cells):raise ValueError('Spanning value cell requires separate review')
            value_text=''.join(normalize(c['text']) for c in cells)
            source_identity=(ti,ri,tuple(columns))
        elif locator['kind']=='quote':
            quote=locator['quote'];value_text=locator['value_text']
            if (text.count(quote)!=1 or quote.count(value_text)!=1 or item['label'] not in quote
                    or len(quote)>4096):raise ValueError('HTML quote is ambiguous or differs')
            source_identity=('quote',quote)
        else:raise ValueError('Unsupported HTML fact locator')
        if source_identity in positions:raise ValueError('HTML source fact selected twice')
        positions.add(source_identity)
        # Exact fixed-point lexical conversion. No missing markers become zero.
        numeric=value_text.removeprefix('$')
        negative=numeric.startswith('(') and numeric.endswith(')')
        if negative:numeric=numeric[1:-1]
        if not re.fullmatch(r'(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?',numeric):
            raise ValueError('HTML numeric cell is ambiguous or missing')
        digits=Decimal(numeric.replace(',','')).as_tuple()
        value=format(Decimal((int(negative),digits.digits,digits.exponent+scale)),'f')
        decimals=str(-digits.exponent-scale)
        if value!=item['expected_value'] or decimals!=item['decimals']:
            raise ValueError('HTML value or precision differs from independently reviewed value')
        observations.append(dict(source_position=position,concept=concept,period_start=start,period_end=end,
            unit=unit,dimensions=dimensions,value=value,decimals=decimals,scale=str(scale),format='',raw_text=value_text,
            source_text_hash=expected_hash,context_id=digest((start,end,unit,dimensions)) if dimensions else digest((start,end,unit)),fact_id='',
            html_proof=dict(plan_hash=seal,locator=locator,label=item['label'],evidence=evidence)))
        if native:observations[-1]['html_proof']['native']=native
    result=dict(version=VERSION,observations=tuple(observations),rejected=(),source_text_hash=expected_hash,
        html_plan=plan,extraction_scope='reviewed_observations',whole_document_coverage=False,
        security_cover=dict(observations=(),rejected=(),class_completeness_proven=False),
        catalog_hash=facts.catalog_hash(catalog),original_vintage_qualified=False,certified=False,allocation_certified=False)
    return dict(result,extraction_hash=digest(result))


def verify_registered(extraction,cik):
    """Independent normalized readback against the immutable reviewed mapping."""
    from .sec_html_reviewed import plan_for
    plan=plan_for(extraction['source_text_hash'],cik)
    body=dict(extraction);seal=body.pop('extraction_hash',None)
    if (not plan or plan!=extraction.get('html_plan') or digest(body)!=seal
            or extraction.get('whole_document_coverage') is not False
            or extraction.get('extraction_scope')!='reviewed_observations'
            or extraction['rejected'] or len(plan['facts'])!=len(extraction['observations'])):
        raise ValueError('HTML reviewed extraction/readback differs')
    for position,(item,row) in enumerate(zip(plan['facts'],extraction['observations'])):
        expected=dict(source_position=position,concept=item['concept'],period_start=item['period_start'],
            period_end=item['period_end'],unit=item['unit'],value=item['expected_value'],decimals=item['decimals'],
            scale=str(item['scale']),source_text_hash=extraction['source_text_hash'])
        if (any(row.get(k)!=v for k,v in expected.items())
                or tuple(tuple(d) for d in row['dimensions'])!=tuple(tuple(d) for d in item.get('dimensions',()))):
            raise ValueError('HTML reviewed semantic observation differs')
        proof=dict(plan_hash=plan['plan_hash'],locator=item['locator'],label=item['label'],evidence=item['evidence'])
        if 'native' in item:
            from .sec_html_native import verify
            proof['native']=verify(item,cik)
        if row['html_proof']!=proof:
            raise ValueError('HTML source location proof differs')

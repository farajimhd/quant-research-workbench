from hashlib import sha256
import pytest
from pipelines.certified_research.sec_fundamental_context import extract


def source(value='-123.50',extra=''):
    return f'''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:g="http://fasb.org/us-gaap/2018" xmlns:iso="http://www.xbrl.org/2003/iso4217">
<x:context id="c"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">940944</x:identifier></x:entity><x:period><x:instant>2018-11-25</x:instant></x:period></x:context>
<x:unit id="u"><x:measure>iso:USD</x:measure></x:unit><g:Assets contextRef="c" unitRef="u" decimals="2" {extra}>{value}</g:Assets></x:xbrl>'''


def run(s):return extract(s,sha256(s.encode()).hexdigest(),'940944')


@pytest.mark.parametrize('value,expected',[('-123.50','-123.50'),('+12.','12.0'),('.25','0.25')])
def test_native_instance_exact_decimal_context_and_original_hash(value,expected):
    s=source(value);r=run(s)
    assert len(r['observations'])==1 and not r['rejected']
    assert r['observations'][0]['value']==expected
    assert r['observations'][0]['unit']=='USD'
    assert r['source_text_hash']==sha256(s.encode()).hexdigest()


@pytest.mark.parametrize('value,extra',[('1,000',''),('1e3',''),('2','scale="3"'),('<b>2</b>','')])
def test_instance_does_not_apply_inline_or_html_recovery(value,extra):
    r=run(source(value,extra));assert not r['observations'] and len(r['rejected'])==1

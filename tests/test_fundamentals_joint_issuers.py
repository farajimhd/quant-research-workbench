from hashlib import sha256
import pytest
from pipelines.fundamentals.joint_issuers import extract,base_sql


def source(value='7323',extra='',other_axis=False):
    axis='dei:OtherAxis' if other_axis else 'dei:LegalEntityAxis'
    return f'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:x="http://www.xbrl.org/2003/instance"
      xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:dei="http://xbrl.sec.gov/dei/2025"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:e="http://example.com/20260331">
      <x:context id="c"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">65984</x:identifier>
      <x:segment><d:explicitMember dimension="{axis}">e:ArkansasMember</d:explicitMember></x:segment></x:entity></x:context>
      <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">{value}</ix:nonNumeric>{extra}</html>'''


def test_explicit_cik_is_preserved_without_name_guessing():
    raw=source();rows=extract(raw,sha256(raw.encode()).hexdigest(),'0000065984')
    assert rows==[dict(issuer_cik='0000007323',axis='{http://xbrl.sec.gov/dei/2025}LegalEntityAxis',member='{http://example.com/20260331}ArkansasMember')]
    with pytest.raises(ValueError):extract(raw,'a'*64,'0000065984')
    with pytest.raises(ValueError):extract(raw,sha256(raw.encode()).hexdigest(),'0000007323')


@pytest.mark.parametrize('raw',[source('unknown'),source(other_axis=True),source(extra='<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">66901</ix:nonNumeric>')])
def test_unknown_dimension_or_conflicting_cik_cannot_be_an_association(raw):
    with pytest.raises(ValueError):extract(raw,sha256(raw.encode()).hexdigest(),'0000065984')


def test_serving_does_not_sum_or_strip_other_dimensions():
    sql=base_sql(['0000007323'],'arte')
    assert "JSONExtract(c.dimensions,'Array(Array(String))')=[[a.axis,a.member]]" in sql
    assert "quality='certified'" in sql and 'source_dimensions' in sql
    assert 'source_id IN' in sql and 'sum(' not in sql

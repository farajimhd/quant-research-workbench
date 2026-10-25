from datetime import datetime, timezone
import pytest
from pipelines.fundamentals import reporting_notices as notices, retained_sec
from pipelines.certified_research import sec_document_payload


@pytest.mark.parametrize('bad', ['membership', 'hash', 'link', 'parent', None])
def test_legacy_notice_binds_exact_primary_and_exhibit(monkeypatch,bad):
    doc=dict(cik='0000000001',accession='0000000001-26-000001',document='ex99.htm',content_sha256='b'*64)
    rows=[dict(document_name=n,document_type=t,content_sha256=h*64,source_version_key='',source_archive_date='2026-01-02')
          for n,t,h in [('primary.htm','6-K','a'),('ex99.htm','EX-99.1','b')]]
    def query(sql):
        if 'sec_filing_v3' in sql:return [] if bad=='parent' else [dict(primary_document='primary.htm')]
        if 'sec_filing_document_v3' in sql and bad=='membership':return rows[:1]
        if bad=='hash':return [rows[0],dict(rows[1],content_sha256='c'*64)]
        return rows
    monkeypatch.setattr(retained_sec,'inspect',lambda q,d:dict(document=d,time_evidence=dict(available_at='2026-01-02 12:00:00.000000')))
    monkeypatch.setattr(sec_document_payload,'load',lambda *a,**k:'<a href="'+('other.htm' if bad=='link' else 'ex99.htm')+'">exhibit</a>')
    if bad:
        with pytest.raises(ValueError):notices.inspect_notice(query,doc)
    else:
        assert notices.inspect_notice(query,doc)['parent']['document']['document']=='primary.htm'


def test_delay_is_historical_evidence_not_permanent_exemption(monkeypatch):
    from hashlib import sha256
    payload='<p>Annual statements delayed.</p>';h=sha256(payload.encode()).hexdigest()
    record=dict(cik='0000000001',accession='a',document='ex.htm',content_sha256=h,
                period_start='2025-01-01',period_end='2025-12-31',quotes=('Annual statements delayed.',))
    monkeypatch.setattr(notices,'NOTICES',(record,))
    monkeypatch.setattr(notices,'inspect_notice',lambda *a:dict(time_evidence=dict(available_at='2026-01-02 12:00:00.000000')))
    monkeypatch.setattr(sec_document_payload,'load',lambda *a,**k:payload)
    out=notices.inspect_notices(None,['0000000001'],datetime(2026,1,3,tzinfo=timezone.utc))['0000000001'][0]
    assert not out['financial_coverage_exemption']
    assert out['status']=='filing_delayed_as_of_notice'
    assert notices.inspect_notices(None,['0000000001'],datetime(2026,1,1,tzinfo=timezone.utc))['0000000001']==[]
    monkeypatch.setattr(sec_document_payload,'load',lambda *a,**k:payload+'changed')
    with pytest.raises(ValueError,match='hash differs'):notices.inspect_notices(None,['0000000001'],datetime(2026,1,3,tzinfo=timezone.utc))

from datetime import datetime, timezone
from hashlib import sha256
import pytest
from pipelines.reference_data import issuer_lineage as m


def test_exact_succession_bytes_identity_and_quotes():
    payload='<p>First predecessor.</p><p>Second successor.</p>'
    review=dict(content_sha256=sha256(payload.encode()).hexdigest(),
                predecessor_name='OLD CO',quotes=('First predecessor.','Second successor.'))
    company=[dict(entity_name='OLD CO',source_file_id='source')]
    m.verify_payload(review,payload,company)
    with pytest.raises(ValueError,match='bytes'):m.verify_payload(review,payload+' ',company)
    with pytest.raises(ValueError,match='identity'):m.verify_payload(review,payload,[])
    with pytest.raises(ValueError,match='identity'):
        m.verify_payload(review,payload,[dict(entity_name='OTHER CO',source_file_id='source')])
    with pytest.raises(ValueError,match='statements'):
        m.verify_payload(dict(review,quotes=('Invented merger.',)),payload,company)


def test_population_extension_is_new_separate_issuer_set(monkeypatch):
    old=dict(families=dict(sec_bulk=['new'],sec_reported=['new'],borrow=['security']),
             provenance=dict(scope='snapshot'),captured_at='now')
    monkeypatch.setattr(m,'verified_relationships',lambda *a:[dict(predecessor_cik='old')])
    extended=m.extend_population(None,old,datetime.now(timezone.utc))
    assert old['families']['sec_bulk']==['new']
    assert extended['families']['sec_bulk']==['new','old']
    assert extended['families']['borrow']==['security']
    assert extended['provenance']['issuer_succession']['aggregation']=='none'
    assert m.extend_population(None,extended,datetime.now(timezone.utc))==extended


def test_unrelated_issuer_does_not_scan_archive():
    assert m.verified_relationships(lambda sql:pytest.fail(sql),['0000000001'],datetime.now(timezone.utc))==[]
    with pytest.raises(ValueError,match='Aware'):
        m.verified_relationships(None,[],datetime.now())

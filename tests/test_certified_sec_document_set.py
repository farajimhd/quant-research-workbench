from hashlib import sha256
import re
import pytest
from pipelines.certified_research import sec_fundamental_context as context, sec_document_set
from tests.test_certified_sec_share_context import document


def extract(primary, resource):
    return context.extract(primary,sha256(primary.encode()).hexdigest(),'320193',resource_documents=[
        dict(payload=resource,source_text_hash=sha256(resource.encode()).hexdigest())])


def test_resources_restore_exact_facts_without_counting_supplemental_facts():
    full=document('123')
    primary=re.sub(r'<x:context\b.*?</x:context>|<x:unit\b.*?</x:unit>','',full,flags=re.S)
    result=extract(primary,full)
    expected=context.extract(full,sha256(full.encode()).hexdigest(),'320193')['observations']
    assert [{k:v for k,v in row.items() if k!='source_text_hash'} for row in result['observations']]==[
        {k:v for k,v in row.items() if k!='source_text_hash'} for row in expected]
    assert len(result['observations'])==1 and not result['rejected']
    assert result['source_text_hash']==sha256(primary.encode()).hexdigest()
    assert result['resource_document_hashes']==(sha256(full.encode()).hexdigest(),)
    with pytest.raises(ValueError,match='incomplete'):
        sec_document_set.validate(result,{},'2026-01-01T00:00:00Z')


def test_changed_resource_hash_rejected():
    full=document()
    with pytest.raises(ValueError,match='hash differs'):
        context.extract(full,sha256(full.encode()).hexdigest(),'320193',resource_documents=[dict(payload=full,source_text_hash='a'*64)])


def test_conflicting_context_is_not_silently_overridden():
    full=document()
    with pytest.raises(ValueError,match='Conflicting'):
        extract(full,full.replace('2025-10-17','2025-10-16'))


def test_foreign_issuer_context_remains_rejected():
    full=document()
    primary=re.sub(r'<x:context\b.*?</x:context>|<x:unit\b.*?</x:unit>','',full,flags=re.S)
    result=extract(primary,full.replace('0000320193','0000000001'))
    assert result['rejected'][0]['reason']=='issuer_context_mismatch'


@pytest.mark.parametrize('field,value', [('submission_hash','other'),
    ('container_sha256','other'),('source_archive_date','2026-01-02'),('document_hash','other'),('document_key','primary')])
def test_resource_proof_cannot_cross_identity(monkeypatch,field,value):
    monkeypatch.setattr(sec_document_set.sec_witness_publication,'records',lambda *args:None)
    primary=dict(submission_hash='s',container_sha256='c',source_archive_date='2026-01-01',document_key='primary')
    witness=dict(primary,document_key='resource',document_hash='hash');witness[field]=value
    proof=dict(witness=witness,qualification=dict(available_at='2026-01-01T00:00:00Z'),receipt={})
    with pytest.raises(ValueError,match='boundary'):
        sec_document_set.validate(dict(resource_document_hashes=['hash'],resource_document_proofs=[proof]),primary,'2026-01-01T00:00:00Z')


def test_resource_availability_cannot_backdate_primary(monkeypatch):
    monkeypatch.setattr(sec_document_set.sec_witness_publication,'records',lambda *args:None)
    primary=dict(submission_hash='s',container_sha256='c',source_archive_date='2026-01-01',document_key='primary')
    proof=dict(witness=dict(primary,document_key='resource',document_hash='hash'),qualification=dict(available_at='2026-01-02T00:00:00Z'),receipt={})
    with pytest.raises(ValueError,match='boundary'):
        sec_document_set.validate(dict(resource_document_hashes=['hash'],resource_document_proofs=[proof]),primary,'2026-01-01T00:00:00Z')

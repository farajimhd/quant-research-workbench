from copy import deepcopy
from hashlib import sha256
import pytest
from pipelines.fundamentals import family_disclosures as family
from tests.test_certified_sec_public_archive_time import inputs,reseal


def fixture():
    payload='<html><p>Subsidiaries: Example Subsidiary LLC</p></html>'
    witness,receipt=inputs();witness['document_hash']=sha256(payload.encode()).hexdigest();witness['candidate_hash']='a'*64;reseal(witness)
    assertion=dict(parent_cik='0000320193',child_name='Example Subsidiary LLC',
        source_accession='0000320193-25-000079',source_document='ex21.htm',source_sha256=witness['document_hash'],
        document_assertion_reviewed=True,relationship='disclosed_subsidiary_direct_or_indirect',ownership_percent=None,
        complete_family=False,valid_from=None,valid_to=None,asserted_as_of='2025-01-01')
    lineage=dict(candidate_hash=witness['candidate_hash'],document=dict(cik='320193',
        accession_number=assertion['source_accession'],document_name='ex21.htm'))
    return assertion,payload,witness,receipt,lineage


def test_verified_name_does_not_invent_entity_or_ownership_interval():
    assertion,payload,witness,receipt,lineage=fixture()
    result=family.verify([assertion],payload,witness,receipt,lineage)
    row=result['rows'][0]
    assert result['verified'] and row['child_cik'] is None and row['identity_status']=='unresolved'
    assert row['ownership_interval_known'] is False and result['completeness']=='unknown'
    assert result['aggregation']=='none'


@pytest.mark.parametrize('field,value',[('parent_cik','0000789019'),('source_document','other.htm'),
    ('child_name','Invented Child'),('ownership_percent',100),('complete_family',True)])
def test_review_cannot_expand_beyond_exact_document(field,value):
    assertion,payload,witness,receipt,lineage=fixture();assertion[field]=value
    with pytest.raises(ValueError):family.verify([assertion],payload,witness,receipt,lineage)

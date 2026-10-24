from datetime import date
import pytest
from pipelines.reference_data.identity_review import reviewed_identity,applicability,METHOD
from pipelines.fundamentals.population import direct_rows


def test_current_evidence_never_backdates_to_listing_inception():
    evidence=[dict(ticker='ABC',cik='0000000001',is_active=1,source_file_id='hash',last_seen_at_utc='2026-08-16 00:00:00')]
    issuers=[dict(cik='0000000001',issuer_id='issuer1')]
    assert reviewed_identity('ABC',evidence,issuers,date(2026,10,5))['valid_from_date']=='2026-08-16'
    with pytest.raises(ValueError):reviewed_identity('ABC',evidence,issuers,date(2026,1,1))
    with pytest.raises(ValueError):reviewed_identity('ABC',evidence+[dict(evidence[0],cik='0000000002')],issuers,date(2026,10,5))
    with pytest.raises(ValueError):reviewed_identity('ABC',evidence,[],date(2026,10,5))


def test_fund_status_is_not_guessed_from_registration_or_shareholder_forms():
    assert applicability(['N-CSR','NPORT-P'])=='fund_specific_not_corporate_v1'
    assert applicability(['10-K','N-CSR'])=='corporate_financials_applicable'
    assert applicability(['S-1','SCHEDULE 13G'])=='unresolved_applicability'
    row=dict(mapping_method=METHOD,mapping_status='active',ambiguity_status='unique')
    assert direct_rows([row])==[row]
    assert direct_rows([dict(row,mapping_status='quarantined')])==[]

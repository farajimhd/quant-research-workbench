from datetime import date
import pytest
from pipelines.reference_data.identity_review import reviewed_identity,applicability,METHOD
from pipelines.fundamentals.population import direct_rows


def test_source_bound_listing_type_distinguishes_fund_from_ticker_reuse():
    evidence=dict(security_id='security:current',security_type='ETF',
                  source_content_sha256='a'*64,last_seen_at_utc='2026-09-10 09:20:25.398')
    assert applicability(['10-K'],security_evidence=evidence)=='fund_specific_not_corporate_v1'
    assert applicability([],security_evidence=dict(evidence,security_type='CS'))=='unresolved_applicability'
    with pytest.raises(ValueError,match='provenance'):
        applicability([],security_evidence=dict(evidence,source_content_sha256=''))

def test_preferred_alias_is_bound_to_exact_security_and_shared_with_rebuild():
    from pipelines.reference_data.identity_review import sec_ticker_sql,SEC_ALIASES
    from pipelines.reference_data.migration.step_06_build_q_live_bridge_features import sec_market_bridge_source_ctes_sql
    expression=sec_ticker_sql()
    assert expression in sec_market_bridge_source_ctes_sql('q_live')
    assert set(SEC_ALIASES)=={'SCHW PRD','SCHW PRJ'}
    for ticker,(alias,security) in SEC_ALIASES.items():
        assert ticker in expression and alias in expression and security in expression


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


def test_missing_issuer_recovers_partial_write_and_rejects_competing_identity():
    import json
    from pipelines.reference_data.identity_review import ensure_retained_issuer
    tables={'id_issuer_v1':[],'id_issuer_identifier_v1':[]};fail=[True]
    def q(sql,read=True):
        if 'company_ticker_v3' in sql:return [dict(cik='0001227857',source_file_id='ticker-proof')]
        if 'company_v3' in sql:return [dict(entity_name='Fund II',source_file_id='company-proof',last_seen_at_utc='2026-08-16 00:00:00.000',state_of_incorporation='MA',sic=None,sic_description=None)]
        if 'system.tables' in sql:return [dict(engine='ReplacingMergeTree',storage_policy='live_market_ssd')]
        if 'system.parts' in sql:return [dict(disk_name='live_market_ssd')]
        if sql.startswith('INSERT'):
            table=sql.split()[2].split('.')[1]
            if table=='id_issuer_identifier_v1' and fail.pop():raise RuntimeError('interrupted')
            tables[table].append(json.loads(sql.split('\n')[1]));return []
        if 'INNER JOIN' in sql:return [dict(issuer_id='issuer:cik:0001227857',cik='0001227857')]
        return tables['id_issuer_identifier_v1' if 'id_issuer_identifier_v1' in sql else 'id_issuer_v1']
    with pytest.raises(RuntimeError):ensure_retained_issuer(q,'NCZ','0001227857','2026-10-24 00:00:00.000')
    fail.append(False)
    assert ensure_retained_issuer(q,'NCZ','0001227857','2026-10-24 00:00:01.000')['written']==1
    assert ensure_retained_issuer(q,'NCZ','0001227857','2026-10-24 00:00:02.000')['written']==0
    tables['id_issuer_identifier_v1'][0]['issuer_id']='competing'
    with pytest.raises(ValueError):ensure_retained_issuer(q,'NCZ','0001227857','2026-10-24 00:00:03.000')


def test_provider_presence_never_becomes_public_or_complete_coverage():
    from pipelines.reference_data.identity_review import coverage_status
    assert coverage_status(['N-CSR'])['status']=='not_applicable_corporate_v1'
    assert coverage_status(['10-Q'],provider_observations=20)['status']=='missing_selected_financials'
    result=coverage_status(['10-Q'],provider_observations=20,provider_identity_verified=True)
    assert result['status']=='recorded_provider_only' and result['public_observations']==0
    assert not coverage_status(['10-Q'],public_observations=1)['complete_feature_coverage']

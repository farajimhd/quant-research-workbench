from pipelines.certified_research import batch_readiness


def fixture():
    return dict(status='blocked',source=dict(first_day='2026-01-02',last_day='2026-10-02',sessions=189),
        source_plan=dict(missing_sources=[],incomplete_sources=[],missing_reporting=['2026-01-02']),
        canonical_mutations=0,universe_preflight=dict(ready=False,rejected=3),
        short_publication_preflight=dict(families={'short_volume':dict(repair_required=True,unknown_public_time=10)}),
        corporate_evidence_preflight=dict(original_public_vintage_certified=False),
        sec_acquisition_preflight=dict(ready=False,gaps=['sec_bulk_companyfacts']),
        admission_blockers=['missing evidence'])


def test_execution_graph_keeps_migration_and_reference_dependencies():
    report=fixture();stages=batch_readiness.stages(report)
    assert stages[2]['parents']==[] # Source acquisition can run alongside identity work.
    assert stages[3]['parents']==[2,3]
    assert stages[3]['market_dependencies']['shared_bars_and_liquidity']==['indicators','causal_v7_PM_REGULAR_AH','activity']
    assert not stages[2]['rest_recovery']
    assert stages[-1]['status']=='waiting' and not stages[-1]['certified']
    assert stages[3]['reference_gaps'][0]['unknown_public_time']==10


def test_prepared_pool_never_claims_completed_products_or_seal():
    report=fixture();report['batch_conid_pool']=dict(summary=dict(resolved=10,event_backed_resolved=8,blocked=2))
    report['execution_stages']=batch_readiness.stages(report)
    assert report['execution_stages'][0]['status']=='prepared'
    assert report['execution_stages'][1]['status']=='blocked'
    text=batch_readiness.render(report)
    assert '10 supported entities' in text and 'No dataset seal' in text
    assert '189 source sessions' in text

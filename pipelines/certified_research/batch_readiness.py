"""Execution dependencies and truthful five-stage batch preparation status."""


def stages(result):
    inputs=result['source_plan'];membership=result['universe_preflight']
    pool=result.get('batch_conid_pool')
    source_gaps={name:len(inputs[name]) for name in ('missing_sources','incomplete_sources','missing_reporting') if inputs[name]}
    reference_gaps=[]
    for family,summary in result['short_publication_preflight']['families'].items():
        if summary['repair_required']:reference_gaps.append(dict(family=family,unknown_public_time=summary['unknown_public_time']))
    if not result['corporate_evidence_preflight']['original_public_vintage_certified']:
        reference_gaps.append(dict(family='corporate_actions',reason='missing_original_public_vintages'))
    if not result['sec_acquisition_preflight']['ready']:
        reference_gaps.append(dict(family='SEC',gaps=result['sec_acquisition_preflight']['gaps']))
    mapped=pool is not None
    return [
        dict(stage=1,name='batch_conid_pool',parents=[],status='prepared' if mapped else 'blocked',
            evidence=pool,reason=None if mapped else 'explicit_sealed_conid_review_required'),
        dict(stage=2,name='dated_eligibility',parents=[1],status='prepared' if mapped and membership['ready'] else 'blocked',
            evidence=membership),
        dict(stage=3,name='canonical_ingestion_and_migrations',parents=[],status='prepared' if not source_gaps and not result['canonical_mutations'] else 'blocked',
            gaps=source_gaps,canonical_mutations=result['canonical_mutations'],
            acquisition='optional_flatfile_download_encode_import',rest_recovery=False),
        dict(stage=4,name='market_and_reference_products',parents=[2,3],status='blocked' if reference_gaps else 'waiting',
            reference_gaps=reference_gaps,
            market_dependencies={'events':['shared_bars_and_liquidity'],
                'shared_bars_and_liquidity':['indicators','causal_v7_PM_REGULAR_AH','activity']},
            reference_dependencies={'certified_SEC':['fundamentals','outstanding_shares','float'],
                'certified_FINRA':['short_volume','short_interest','short_pressure']}),
        dict(stage=5,name='verify_and_seal',parents=[1,2,3,4],status='waiting',
            certified=False,reason='requires_completed_product_receipts_and_independent_readback'),
    ]


def render(result):
    source=result['source']
    lines=[f"Batch preparation | {source['first_day']} to {source['last_day']} | {source['sessions']} source sessions | {result['status']}"]
    for stage in result['execution_stages']:
        lines.append(f"  {stage['stage']}. {stage['name']} | {stage['status']}")
    pool=result.get('batch_conid_pool')
    if pool:
        summary=pool['summary']
        lines.append(f"Conid pool | {summary['resolved']:,} supported entities | {summary['event_backed_resolved']:,} event-backed | {summary['blocked']:,} unresolved entities retained")
    lines.append('Admission blockers:')
    lines.extend('  '+reason for reason in result['admission_blockers'])
    lines.append('No dataset seal or downstream training/optimization was produced by this inspection.')
    return '\n'.join(lines)

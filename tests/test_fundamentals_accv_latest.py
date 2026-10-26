from pipelines.certified_research.contracts import digest
from pipelines.certified_research.sec_html_reviewed import plan_for


def test_latest_accv_preserves_all_reviewed_contexts_and_predecessor_periods():
    h='6c03c547f35b02f4f6681c3510bf934586acacae04e625f6f7cdc64a51285a86'
    p=plan_for(h,'0002141406')
    assert p and len(p['facts'])==41
    assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
    assert plan_for(h,'0002056611') is None
    annual=[f for f in p['facts'] if f['locator']['table']==72]
    assert len(annual)==6
    assert {(f['period_start'],f['period_end']) for f in annual}=={
        ('2025-01-02','2025-12-31'),('2024-01-01','2024-12-31')}
    assert all(f['unit']=='source:$' and f['scale']==3 for f in annual)
    revenue={f['period_end']:f['expected_value'] for f in annual if f['label']=='Revenue'}
    assert revenue=={'2025-12-31':'447819000','2024-12-31':'181350000'}

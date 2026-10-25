from pipelines.certified_research.sec_html_reviewed import plan_for
from pipelines.certified_research.sec_html_reviewed_owls import REVIEWED


def test_owls_parent_totals_and_native_cash_have_distinct_semantics():
    assert len(REVIEWED)==3
    for document,plan in REVIEWED.items():
        assert plan_for(document,'0002025416')==plan
        assert plan_for(document,'0002025417') is None
        assert len(plan['facts'])==28
        current=[f for f in plan['facts'] if f['period_end']=='2024-12-31']
        equity=next(f for f in current if f['concept'].endswith('}EquityAttributableToOwnersOfParent'))
        income=next(f for f in current if f['concept'].endswith('}ProfitLossAttributableToOwnersOfParent'))
        assert equity['expected_value']=='-4298042'  # Excludes non-controlling interest.
        assert income['expected_value']=='-10269908'  # Parent, not consolidated net loss.
        native={f['label']:f for f in current if 'native' in f}
        assert native['Cash']['expected_value']=='4511377'
        assert native['Acquisition of property, plant and equipment']['expected_value']=='-151964'
        assert all(f['unit']=='USD' for f in native.values())
        assert all(f['period_start']=='2024-01-01' for f in current if f['period_start'])

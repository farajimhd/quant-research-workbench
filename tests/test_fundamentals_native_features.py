from datetime import datetime, timezone
import pytest
from pipelines.fundamentals import native_features
from pipelines.fundamentals.native_feature_mappings import MAPPINGS
from pipelines.certified_research.sec_html_reviewed import plan_for
from pipelines.certified_research.sec_taxonomy import identity


def test_every_mapping_is_exact_reviewed_issuer_concept_and_currency():
    keys=[]
    for cik,source,document,metric,unit,feature,serving_unit,currency_evidence in MAPPINGS:
        plan=plan_for(document,cik)
        assert plan and len(source)==64
        matches=[f for f in plan['facts'] if ':'.join(identity(f['concept'],allow_extension=True))==metric]
        assert matches and all(f['unit']==unit and not f.get('dimensions') for f in matches)
        assert serving_unit in ('CNY','HKD','USD')
        assert unit==serving_unit or (unit=='source:$' and currency_evidence and serving_unit=='USD')
        assert feature!='equity_parent'
        if feature=='net_income_parent':
            assert cik=='0002141616'
            assert all(f['label']=='Net loss attributable to Amaero Inc. stockholders' for f in matches)
        keys.append((cik,source,metric,unit))
    assert len(keys)==len(set(keys))


def test_unreviewed_issuer_has_no_extra_serving_scan():
    assert native_features.base_sql(['0000320193'],'arte') is None


def test_mapping_conflicts_fail_closed(monkeypatch):
    monkeypatch.setattr(native_features,'MAPPINGS',(MAPPINGS[0],MAPPINGS[0]))
    with pytest.raises(ValueError,match='Competing'):
        native_features.base_sql([MAPPINGS[0][0]],'arte')


def test_known_time_and_receipt_boundary_are_required():
    sql=native_features.base_sql(['0002046919'],'arte',datetime(2026,1,1,tzinfo=timezone.utc))
    assert 'observed_at<=' in sql and 'reference_batch_receipt_v1 FINAL' in sql
    assert "quality='certified'" in sql and 'c.unit=m.unit' in sql
    assert 'f.source_id=m.source_id' in sql and 'f.metric=m.metric' in sql

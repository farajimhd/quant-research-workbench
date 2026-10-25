from datetime import datetime,timezone
import pytest
from pipelines.fundamentals.native_observations import financial_sql

NOW=datetime(2026,10,25,tzinfo=timezone.utc)


def test_native_reader_preserves_entity_scope_and_unresolved_currency():
    sql=financial_sql(['0002141406'],NOW)
    assert 'd.statement_scope' in sql and 'd.issuer_cik' in sql
    assert "'unresolved_source_symbol'" in sql and 'c.unit' in sql
    assert "quality='certified'" in sql and 'reference_batch_receipt_v1 FINAL' in sql
    assert 'groupUniqArray(source_id)' in sql
    assert 'sum(' not in sql and 'argMax(' not in sql


def test_history_has_per_context_seed_and_explicit_knowledge_clock():
    since=datetime(2025,1,1,tzinfo=timezone.utc)
    sql=financial_sql(['0002141406'],NOW,since=since,known_at=NOW)
    assert 'observed_at<=' in sql and 'available_at>=' in sql and 'available_at<' in sql
    assert 'PARTITION BY cik,metric,context_id ORDER BY available_at DESC' in sql
    with pytest.raises(ValueError):financial_sql(['0002141406'],NOW,since=NOW)
    with pytest.raises(ValueError):financial_sql(['0002141406'],NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):financial_sql(['0002141406']*33,NOW)

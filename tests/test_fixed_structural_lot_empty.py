"""Real candidate decoder and controlled installation boundary for empty days."""
from dataclasses import replace
from datetime import date
import json
from uuid import uuid4

import pytest

from test_backtest_strategy_one_candidate_store import Reader,_market,THROUGH,EMPTY_HASH
from test_fixed_structural_lot_native import declarations
from src.backend import backtest_fixed_structural_lot_empty as empty
from src.backend import backtest_fixed_structural_lot_native as native
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan,RULE_DIGEST


class EmptyReader(Reader):
    def execute(self,query):
        text=super().execute(query)
        if 'FROM arte.strategy_one_candidate_coverage_v1' in query:
            rows=[json.loads(v) for v in text.splitlines()]
            for row in rows:row.update(candidate_count=0,content_hash=EMPTY_HASH)
            return '\n'.join(json.dumps(row) for row in rows)
        if 'FROM arte.strategy_one_candidate_v1' in query:return ''
        return text


def source_fixture(monkeypatch,*,reader=None,candidates=None):
    reader=reader or EmptyReader();market=_market()
    candidates=candidates or certify_candidate_plan(market,candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=THROUGH,client=reader)
    parent,own,release,_=declarations()
    calls=[]
    # Full market and own installation are explicit controlled seams; the
    # candidate SELECT/coverage/content decoder below remains production code.
    import src.backend.backtest_market_data as market_module
    import src.backend.backtest_input_scope as scope_module
    monkeypatch.setattr(market_module,'verify_market_day_plan',lambda *a,**kw:calls.append('market'))
    monkeypatch.setattr(scope_module,'input_exclusions',lambda day:())
    monkeypatch.setattr(native,'numbered_strategy_parent',lambda number:42)
    monkeypatch.setattr(native,'certify_numbered_configuration',lambda client,number:parent)
    monkeypatch.setattr(native,'load_installed_configuration',lambda client,**kw:calls.append('own') or (own,native.parse_fixed_structural_lot_policy(own.payload['strategy']['parameters']['fixed_structural_lot_policy']),'e'*64))
    monkeypatch.setattr(native,'verify_current_installed_source',lambda value:calls.append('source'))
    source=empty.prepare_empty_fixed_structural_lot_source(reader,number=release.number,
        run_id=str(uuid4()),session_date=date.fromisoformat(market.sessions[0]),
        market=market,candidates=candidates,through_boundary_ms=THROUGH)
    return source,calls,reader


def test_empty_factory_uses_actual_complete_candidate_decoder_without_geometry(monkeypatch):
    source,calls,reader=source_fixture(monkeypatch)
    source.require_prepared_source();source.require_installed_admission()
    assert calls==['market','own','source']
    assert len(source.candidates.coverage)==2 and not source.candidates.prepared
    assert source.intervals is None and source.price_authority is None
    assert all(q.startswith('SELECT') for q in reader.queries)
    assert sum('FROM arte.strategy_one_candidate_coverage_v1' in q for q in reader.queries)==2
    with pytest.raises(ValueError,match='grants no entry'):
        native.NativeFixedStructuralLotOperation(source).source.request(object())


@pytest.mark.parametrize('field,value',[('run_id','foreign'),('through_boundary_ms',100),
    ('installed_json','{}'),('selected_json','{}')])
def test_copied_or_resealed_empty_source_has_no_factory_authority(monkeypatch,field,value):
    source,_,_=source_fixture(monkeypatch)
    for forged in (replace(source),replace(source,**{field:value})):
        with pytest.raises(ValueError,match='factory certified'):forged.require_installed_admission()


def test_missing_coverage_is_not_certified_emptiness(monkeypatch):
    with pytest.raises((ValueError,RuntimeError)):
        source_fixture(monkeypatch,reader=EmptyReader(missing=True))


def test_real_nonempty_candidate_horizon_cannot_issue_empty_source(monkeypatch):
    with pytest.raises(ValueError,match='genuine complete candidate source'):
        source_fixture(monkeypatch,reader=Reader())

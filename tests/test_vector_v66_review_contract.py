from dataclasses import replace
import json
import numpy as np
import pytest
from research.vectorized_backtest.v66 import review_contract as rc
from research.vectorized_backtest.v66.genome import sample, Network, Neuron
from research.vectorized_backtest.v66.authority import construct, ALLOWED
from research.vectorized_backtest.v66.lineage import program_key


def setup(tmp_path):
    m=sample(np.random.default_rng(38),1)[0]
    m=construct(replace(m,networks=dict(m.networks,entry=Network((Neuron((ALLOWED[0],),(1.,),0.,op='identity'),)))))
    parent=dict(serial=7,payload=m.payload(),record=dict(program_sha256=program_key(m)),
        sessions={'2026-01-01':dict(closed_positions=2,winning_positions=1,net_pnl=0),
                  '2026-01-02':dict(closed_positions=0,winning_positions=0,net_pnl=0)})
    (tmp_path/'parents.json').write_text(json.dumps([parent]))
    row=rc.diagnose(parent)
    review=dict(version=rc.VERSION,scope='training',source_generation=20,evidence={},
        parents_sha256=rc.sha(tmp_path/'parents.json'),candidates=[row])
    package=dict(source_generation=20,evidence={},offspring=[])
    return row,review,package


def test_zero_net_trades_count_as_active(tmp_path):
    row,_,_=setup(tmp_path)
    assert row['breadth']['traded_sessions']==1
    assert row['breadth']['ticker_coverage'] is None


def test_price_named_feature_retains_signed_ratio_contract():
    from research.vectorized_backtest.v66.genome import CATALOG
    source=next(i for i,f in enumerate(CATALOG) if f.name=='arte.trade.86400000ms.lag2.high')
    contract=rc.input_contract(Network((Neuron((source,),(1.,),0.,op='cross'),)))
    assert contract[0]['unit']=='ratio' and contract[0]['lower']<0
    assert contract[0]['channel']=='absolute_transformed'


def test_incomplete_reviews_cannot_publish(tmp_path):
    _,review,package=setup(tmp_path)
    with pytest.raises(ValueError):rc.validate_coverage(tmp_path,package,review)


def test_honest_deferral_but_not_untested_offspring(tmp_path):
    row,review,package=setup(tmp_path)
    row['decision']='defer'
    for c in [*row['endpoints'].values(),*row['checks'].values()]:c['reason']='No trace available'
    with pytest.raises(ValueError,match='unfinished'):rc.validate_coverage(tmp_path,package,review)
    row['decision']='repair';package['offspring']=[dict(parent=7)]
    with pytest.raises(ValueError,match='untested'):rc.validate_coverage(tmp_path,package,review)


def test_omitted_endpoint_and_changed_identity_rejected(tmp_path):
    row,review,package=setup(tmp_path)
    row['endpoints'].pop(next(iter(row['endpoints'])))
    with pytest.raises(ValueError):rc.validate_coverage(tmp_path,package,review)
    review['parents_sha256']='changed'
    with pytest.raises(ValueError,match='identity'):rc.validate_coverage(tmp_path,package,review)


def test_fabricated_evidence_rejected(tmp_path):
    row,review,package=setup(tmp_path);row['decision']='defer'
    for c in [*row['endpoints'].values(),*row['checks'].values()]:
        c.update(status='tested',reason='Measured',evidence=[])
    with pytest.raises(ValueError,match='evidence'):rc.validate_coverage(tmp_path,package,review)

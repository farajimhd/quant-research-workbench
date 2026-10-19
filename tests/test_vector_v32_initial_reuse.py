import json
import numpy as np
import pytest
from research.vectorized_backtest.v32.initial_reuse import import_receipt,VERSION,SOURCES
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash
from test_vector_v32_two_phase import metrics


def test_sealed_initial_reuse_rescores_without_old_fitness_and_rejects_corruption(tmp_path):
    day='2026-07-30';contract=dict(day=day,seed=2020,population=1024,initial_multiplier=6,
        dataset={},execution={},feature_semantics='v21-magnitude-ratio-v1',backend='compile',device='cuda',batch_size=1024)
    prior=dict(contract,version='v31-local-session-v1',source_sha256=next(iter(SOURCES)))
    record=dict(day=day,split='training',validation_opened=False,full_session=True,
        population_sha256='old',identity={},execution={},metrics=metrics(6144))
    receipt=tmp_path/'receipt.json';seal=tmp_path/'seal.json';write_json(receipt,record)
    write_json(seal,dict(receipt_sha256=file_hash(receipt),population_sha256='old'))
    manifest=dict(version=VERSION,validation_opened=False,sessions={day:dict(contract=prior,
        receipt=str(receipt),receipt_sha256=file_hash(receipt),seal=str(seal),seal_sha256=file_hash(seal),population_sha256='old')})
    assert not import_receipt(manifest,'2026-07-31',contract,'new',6144,tmp_path/'missing',{},'old')
    assert not import_receipt(manifest,day,contract,'new',6144,tmp_path/'mismatch',{},'different')
    assert import_receipt(manifest,day,contract,'new',6144,tmp_path/'out',{},'old')
    imported=json.loads((tmp_path/'out/generation-0001'/day/'receipt.json').read_bytes())
    assert imported['population_sha256']=='new' and imported['metrics']==record['metrics']
    assert imported['reuse_provenance']['old_scores_imported'] is False
    from research.vectorized_backtest.v32.objectives import local_panel
    np.testing.assert_allclose(local_panel([imported['metrics']])['values'][0],np.arange(1,6145)*.975)
    write_json(receipt,dict(record,full_session=False))
    with pytest.raises(ValueError,match='evidence changed'):
        import_receipt(manifest,day,contract,'new',6144,tmp_path/'bad',{},'old')


def test_version_translation_preserves_initial_payloads_and_rng():
    from research.vectorized_backtest.v31.genome import sample as old
    from research.vectorized_backtest.v32.genome import sample as new
    a=np.random.default_rng(2020);b=np.random.default_rng(2020)
    for _ in range(10):
        x=old(a,1)[0].payload();y=new(b,1)[0].payload()
        assert x==dict(y,version='v31-recurrent-v1')
    assert a.bit_generator.state==b.bit_generator.state

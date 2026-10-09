import json
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.profile_cpu_scaling import compare_prefix
from research.vectorized_backtest.v6.torch_backtest.profile_sparse import profile_metrics
from research.vectorized_backtest.v6.torch_backtest.sparse_runner import SparseProgramRunner
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture


def test_gpu_full_ledger_projects_exact_prefix_without_using_future_fills(tmp_path):
    _,inputs,space,member,gates=fixture()
    full=SparseProgramRunner(inputs,space,[member],gates,maximum_fills=512);full.run()
    prefix=SparseProgramRunner(inputs,space,[member],gates,maximum_fills=512);metrics=prefix.run(steps=20)
    reference=tmp_path/'gpu'/'serial-warm'/'synthetic';batch=reference/'batch-000000';batch.mkdir(parents=True)
    (reference.parent.parent/'population.json').write_text(json.dumps([member.payload()]))
    before=dict(input_receipt_sha256='input',structural_receipt_sha256='structure')
    (batch/'receipt.json').write_text(json.dumps(before))
    torch.save(dict(ledger=full.ledger,counts=full.fill_count),batch/'fills.pt')
    assert int(full.fill_count[0])>int(prefix.fill_count[0])
    actual=tmp_path/'cpu';actual.mkdir()
    source=tmp_path/'inputs'/'synthetic';source.mkdir(parents=True)
    (source/'complete.json').write_text(json.dumps(dict(identity=dict(session=dict(start='1970-01-01T00:00:00+00:00')))))
    cpu=before|dict(full_session=False,validation_opened=False,day='synthetic',arguments=dict(seconds=20,inputs=str(source.parent)),metrics=profile_metrics(metrics))
    (actual/'receipt.json').write_text(json.dumps(cpu));(actual/'population.json').write_text(json.dumps(dict(population=[member.payload()])))
    torch.save(dict(ledger=prefix.ledger,counts=prefix.fill_count),actual/'fills.pt')
    result=compare_prefix(reference,actual,gpu=True)
    assert result['numeric_values_exact'] and not result['full_session']
    changed=torch.load(actual/'fills.pt',weights_only=True);changed['ledger'][0,0,4]+=1
    torch.save(changed,actual/'fills.pt')
    with pytest.raises(ValueError,match='economic event'):compare_prefix(reference,actual,gpu=True)

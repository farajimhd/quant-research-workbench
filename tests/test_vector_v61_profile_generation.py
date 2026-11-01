import json
import pytest
from research.vectorized_backtest.v61.profile_generation import initial_caches
from research.vectorized_backtest.v61.publication import write_json
from research.vectorized_backtest.v61.distributed_assets import sha


def test_qualification_cache_requires_sealed_scope(tmp_path):
    folder=tmp_path/'saved/generation-0001';folder.mkdir(parents=True)
    seal={};job={}
    for split in ('training','validation'):
        day='2026-08-13';job[split+'_days']=[day]
        receipt=tmp_path/(split+'-receipt.json')
        write_json(receipt,dict(day=day,result_identity=dict(source='old',split=split)))
        path=folder/(split+'.json')
        write_json(path,dict(receipts={day:dict(receipt=str(receipt),receipt_sha256=sha(receipt))}))
        seal[split+'_sha256']=sha(path)
    write_json(folder/'complete.json',seal)
    state=dict(completed=1,seals={'1':sha(folder/'complete.json')})
    transitions=initial_caches(folder.parent/'checkpoint.json',state,tmp_path/'output',job)
    assert len(transitions)==2
    assert all(v=={'source':'old'} for v in transitions.values())
    target=tmp_path/'output/phase-2/replays/training/result-cache/2026-08-13.json'
    assert json.loads(target.read_bytes())['receipt_sha256']==sha(tmp_path/'training-receipt.json')
    write_json(tmp_path/'training-receipt.json',{})
    with pytest.raises(ValueError,match='receipt changed'):
        initial_caches(folder.parent/'checkpoint.json',state,tmp_path/'other',job)

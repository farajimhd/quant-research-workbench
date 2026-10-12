import pytest
import torch

from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.run_feedback_benchmark import load_shard


def fixture(tmp_path):
    fields=('raw','present','held','multi','multi_present','liquidity','market')
    sequence={k:torch.zeros(2,9,1) for k in fields}
    sequence['clocks_us']=torch.arange(9,dtype=torch.int64)[None].repeat(2,1)*1_000_000
    sequence['max_input_close_us']=sequence['clocks_us']-1
    data={k:sequence[k][:,-1].clone() for k in fields};data['keys']=['A','B']
    path=tmp_path/'test.pt';torch.save(dict(sequence=sequence,original_rows=[0,1],keys=data['keys']),path)
    receipt=dict(path=str(path),sha256=file_hash(path),rows=2)
    return path,receipt,data,sequence


def test_authenticated_shard_load_and_byte_corruption(tmp_path):
    path,receipt,data,_=fixture(tmp_path)
    assert load_shard(receipt,tmp_path,data)[0]==[0,1]
    with path.open('ab') as stream:stream.write(b'corrupt')
    with pytest.raises(ValueError,match='hash'):load_shard(receipt,tmp_path,data)


def test_final_row_changes_and_future_inputs_fail_closed(tmp_path):
    path,receipt,data,sequence=fixture(tmp_path)
    data['raw'][0,0]=1
    with pytest.raises(ValueError,match='Current inputs'):load_shard(receipt,tmp_path,data)
    data['raw'][0,0]=0
    sequence['max_input_close_us'][0,3]=sequence['clocks_us'][0,3]
    torch.save(dict(sequence=sequence,original_rows=[0,1],keys=data['keys']),path)
    receipt['sha256']=file_hash(path)
    with pytest.raises(ValueError,match='cutoff'):load_shard(receipt,tmp_path,data)

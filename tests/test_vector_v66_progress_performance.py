import hashlib
import json
from types import SimpleNamespace
from rich.console import Console
import pytest
from research.vectorized_backtest.v66.progress_performance import load,table
from research.vectorized_backtest.v66.campaign_progress import Model


def evidence(tmp_path):
    folder=tmp_path/'generation-0005';folder.mkdir();seal={'population_sha256':'same'}
    for role,ids in [('training',[12,13]),('validation',[12,13])]:
        data={'ids':ids,'population_sha256':'same','receipts':{'a':{},'b':{}},'panel':{
            'score':[9,0],'net_pnl':[10,0],'gross_losses':[2,0],'drawdown':[3,0],
            'closed_positions':[4,0],'winning_positions':[3,0],'profitable_sessions':[1,0],
            'net_excluding_best_session':[-2,0]}}
        raw=json.dumps(data).encode();(folder/(role+'.json')).write_bytes(raw)
        seal[role+'_sha256']=hashlib.sha256(raw).hexdigest()
    (folder/'complete.json').write_text(json.dumps(seal))
    return folder


def test_denominators_and_no_trades(tmp_path):
    evidence(tmp_path);data=load(tmp_path,5)
    assert data[12]['training']['win_rate']==75
    assert data[12]['validation']['profitable_percent']==50
    assert data[13]['training']['win_rate'] is None
    console=Console(record=True,width=180)
    console.print(table([{'serial':12},{'serial':13}],data,0,0,{'training':150,'validation':38}))
    text=console.export_text()
    assert '1/2 of 150' in text and '1/2 of 38' in text
    assert '75.00' in text and 'Net ex best' in text


def test_tampering_fails_closed(tmp_path):
    folder=evidence(tmp_path)
    (folder/'validation.json').write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):load(tmp_path,5)


def test_pending_status_does_not_start_generation_zero(tmp_path):
    model=SimpleNamespace(global_root=lambda:tmp_path,status=lambda:{'completed_generations':0},global_data=None)
    assert Model.global_snapshot(model) is None

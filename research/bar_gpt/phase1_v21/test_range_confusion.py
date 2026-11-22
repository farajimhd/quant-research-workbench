import copy
import numpy as np
import pytest
from .range_confusion_audit import compare


def fixture():
    x=np.zeros((6,3,11,11));x[0,0,8,8]=10
    moves=dict(bins=list(range(11)),families=['trade','bid','ask'],
        timeframes=['100ms','1s','10s','1m','1h','daily'],confusion={'range':x.tolist()})
    return {mode:dict(moves=copy.deepcopy(moves)) for mode in ('masked','all_visible','bar_probe')}


def test_accuracy_and_target_counts():
    a=fixture();b=copy.deepcopy(a)
    b['masked']['moves']['confusion']['range'][0][0][8][8]=7
    b['masked']['moves']['confusion']['range'][0][0][8][7]=3
    rows=compare(a,b)
    assert rows[0]['accuracy_delta_pp']==-30
    assert rows[1]['accuracy_delta_pp']==0
    b['masked']['moves']['confusion']['range'][0][0][8][7]=4
    with pytest.raises(ValueError,match='Unmatched target'):compare(a,b)

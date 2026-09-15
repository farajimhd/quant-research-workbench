import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_enrich_supervision import enrich


class Market:
    def at(self,symbol,decision,at):
        assert symbol=='X' and at==10
        return {'causal':1},{'1s':{'status':'measured'}}


def example():
    row=dict(id='r:5',run_id='r',symbol='X',window='w',at=10,features={'future_label':999},label='winner')
    decision=dict(ticker='X',source_signal_ids=['qmd-derived:X:1s:1'],metadata={},reason='waiting')
    return row,decision


def test_research_features_and_labels_do_not_enter_new_feature_vector():
    row,decision=example()
    result=enrich(row,5,'1970-01-01T00:00:10+00:00',decision,Market())
    assert result['features']=={'causal':1}
    assert 'label' not in result
    assert result['id']==row['id']


@pytest.mark.parametrize('change',[{'ticker':'Y'},{'source_signal_ids':['qmd-derived:X:5s:1']}])
def test_source_mismatch_fails(change):
    row,decision=example();decision.update(change)
    with pytest.raises(ValueError,match='symbol or source'):enrich(row,5,'1970-01-01T00:00:10+00:00',decision,Market())


def test_future_or_wrong_sequence_fails():
    row,decision=example()
    for sequence,stamp in ((6,'1970-01-01T00:00:10+00:00'),(5,'1970-01-01T00:00:11+00:00')):
        with pytest.raises(ValueError,match='identity/time'):enrich(row,sequence,stamp,decision,Market())

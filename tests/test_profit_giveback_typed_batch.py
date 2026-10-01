from datetime import date
from dataclasses import replace
from uuid import UUID
import pytest
from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch,project_profit_giveback
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import intent,financial,ENTRY


def unit(strategy_number=31):
    x=intent(strategy_number=strategy_number)
    base=strategy_intent_batch(x,run_id=str(UUID(int=1)),run_month=date(2026,8,1),
        account_id=financial().account_id,attempt_id=str(UUID(int=2)),batch_id=str(UUID(int=3)),
        prior_batch_id=str(UUID(int=4)),sequence=10,source_cursor='cursor',
        run_status='running',recorded_at=x.event_time)
    row=project_profit_giveback(profit_giveback(sample()),x,financial(),session_date=date(2026,8,4),
        source_entry_intent_id=ENTRY,run_id=base.run_id,batch_id=base.batch_id,
        parent_record_id=base.events[0]['record_id'],source_manager_snapshot_id=str(UUID(int=5)),
        source_manager_checkpoint_sequence=7, strategy_number=strategy_number)
    return base,row


@pytest.mark.parametrize('number', [31, 32, 33])
def test_exact_unit_is_immutable_and_checkpoint_precedes_exit(number):
    base,row=unit(strategy_number=number);batch=V4ProfitGivebackBatch(base,row)
    assert batch.profit['source_manager_checkpoint_sequence']==7
    row['prior_high_int']=1
    assert batch.profit['prior_high_int']==110000
    with pytest.raises(TypeError):batch.profit['prior_high_int']=1


@pytest.mark.parametrize('number', [31, 32, 33])
def test_child_version_must_match_parent_profit_reason(number):
    base,row=unit(strategy_number=number)
    row['strategy_number']=(31 if number != 31 else 32)
    with pytest.raises(ValueError, match='intent or prior checkpoint'):
        V4ProfitGivebackBatch(base,row)


@pytest.mark.parametrize('field,value',[('run_id','other'),('parent_record_id',str(UUID(int=99))),
                                      ('source_manager_checkpoint_sequence',10),('source_manager_checkpoint_sequence',True),
                                      ('prior_high_int',109999)])
def test_changed_parent_checkpoint_or_predicate_is_rejected(field,value):
    base,row=unit();row[field]=value
    with pytest.raises(ValueError):V4ProfitGivebackBatch(base,row)


def test_sealed_or_opaque_extra_fields_are_not_accepted():
    base,row=unit();row['content_hash']='a'*64
    with pytest.raises(ValueError,match='unsealed'):V4ProfitGivebackBatch(base,row)

from datetime import datetime,timezone,timedelta
from decimal import Decimal
import pytest

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_journal_writer import typed_contract_row,typed_row,verify_typed_contract_row,_canonical_typed_content,_CONTRACTS


SCHEMA=TableContract('uninstalled_projection_test_v1',(
    ('record_id','UUID'),('event_time',"DateTime64(6, 'UTC')"),
    ('amount','Decimal(38, 18)'),('clock','UInt64'),('content_hash','FixedString(64)'),
),'toYYYYMM(event_time)','record_id')


def values():
    return dict(record_id='11111111-1111-4111-8111-111111111111',
        event_time=datetime(2026,8,4,12,59,59,999999,tzinfo=timezone.utc),amount=Decimal('1.25'),clock=32399999999)


def test_explicit_projection_uses_lossless_canonical_hash_without_family_admission():
    result=typed_contract_row(SCHEMA,values())
    changed=values()
    changed['amount']='1.250000000000000000'
    changed['event_time']=changed['event_time'].astimezone(timezone(timedelta(hours=-4)))
    assert typed_contract_row(SCHEMA,changed)['content_hash']==result['content_hash']
    assert _canonical_typed_content(SCHEMA.name,values(),_contract=SCHEMA)['event_time']=='2026-08-04 12:59:59.999999'
    assert SCHEMA.name not in _CONTRACTS
    with pytest.raises(ValueError,match='Unknown typed journal family'):typed_row(SCHEMA.name,values())


@pytest.mark.parametrize('field,value',[
    ('amount','1.0000000000000000001'),('clock',True),
    ('event_time','2026-08-04T12:59:59.999999001+00:00'),('record_id','foreign'),
    ('content_hash','f'*64),
])
def test_explicit_projection_cannot_truncate_timestamps_money_or_supply_hash(field,value):
    row=values();row[field]=value
    with pytest.raises(ValueError):typed_contract_row(SCHEMA,row)


def test_explicit_recovery_preserves_money_and_microseconds_without_admission():
    before=dict(_CONTRACTS)
    row=typed_contract_row(SCHEMA,values())
    canonical=verify_typed_contract_row(SCHEMA,row)
    assert canonical['amount']=='1.250000000000000000'
    assert canonical['event_time']=='2026-08-04 12:59:59.999999'
    assert verify_typed_contract_row(SCHEMA,canonical,stored_utc=True)==canonical
    assert _CONTRACTS==before and SCHEMA.name not in _CONTRACTS
    with pytest.raises(ValueError):verify_typed_contract_row(SCHEMA,canonical)


@pytest.mark.parametrize('defect',['content','hash','extra','missing','transport'])
def test_explicit_recovery_rejects_changed_content_or_transport(defect):
    row=typed_contract_row(SCHEMA,values());options={}
    if defect=='content':row['clock']+=1
    if defect=='hash':row['content_hash']='F'*64
    if defect=='extra':row['unknown']=1
    if defect=='missing':row.pop('amount')
    if defect=='transport':options['stored_utc']=1
    with pytest.raises(ValueError):verify_typed_contract_row(SCHEMA,row,**options)

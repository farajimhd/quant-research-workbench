"""Compact measurement-date price facts from verified normalized bar parents."""
from __future__ import annotations

from uuid import UUID

from .contracts import identifier,literal,digest
from .float_data import key
from pipelines.market_sip.events.market_day_sql import bounds

VERSION='strict-regular-close-v1'


def calendar_close(day):
    import exchange_calendars
    import pandas as pd
    calendar=exchange_calendars.get_calendar('XNYS')
    stamp=pd.Timestamp(day)
    if not calendar.is_session(stamp):raise ValueError('Price day is not an exchange session')
    return calendar.session_close(stamp).to_pydatetime()


def parent_hash(parents):
    return digest(dict(version=VERSION,parents=[dict(day=str(scope.session_date),listing=scope.listing_key,
        hash=receipt['output_hash'],close=calendar_close(scope.session_date).isoformat()) for scope,receipt in parents]))


def projection(database,dataset_id,attempt_id,security_key,parents,*,wide=False):
    identifier(database);UUID(dataset_id);UUID(attempt_id)
    if not parents or len(parents)>1000 or type(security_key) is not int or not 0<security_key<2**64:
        raise ValueError('Invalid bounded regular-price input scope')
    if len({scope.session_date for scope,_ in parents})!=len(parents):raise ValueError('Competing regular-price listing/day parents')
    calendars=[];keys=[]
    for scope,receipt in parents:
        if scope.product!='bars' or scope.dataset_id!=dataset_id:raise ValueError('Regular price requires same-dataset bar parents')
        UUID(receipt['attempt_id'])
        day=scope.session_date;close=calendar_close(day)
        end=int(close.timestamp()*1_000_000);opening=bounds(day,'09:30:00');origin=bounds(day,'00:00:00')
        calendars.append(f'(toDate({literal(day)}),toUInt64({scope.listing_key}),toUUID({literal(receipt["attempt_id"])}),'
            f'toUInt64({opening}),toUInt64({end}),toUInt64({origin}),{literal(receipt["output_hash"])})')
        keys.append(f'(toDate({literal(day)}),{scope.listing_key},toUUID({literal(receipt["attempt_id"])}))')
    eligible='b.price_valid=1 AND b.close_int>0 AND c.calendar.6+b.bucket_index*1000000>=c.calendar.4 AND c.calendar.6+(b.bucket_index+1)*1000000<=c.calendar.5'
    prefix=f"""WITH selected AS (
      SELECT c.calendar calendar,countIf({eligible}) observations,
        argMaxIf(tuple(b.close_int,b.bucket_index),b.bucket_index,{eligible}) last
      FROM (SELECT arrayJoin([{','.join(calendars)}]) calendar) c LEFT JOIN
        (SELECT * FROM {database}.research_bars_v1 WHERE dataset_id=toUUID({literal(dataset_id)}) AND resolution_ms=1000
          AND (session_date,listing_key,attempt_id) IN ({','.join(keys)})) b
        ON b.session_date=c.calendar.1 AND b.listing_key=c.calendar.2 AND b.attempt_id=c.calendar.3 GROUP BY calendar
    ), prepared AS (
      SELECT *,toJSONString(tuple({literal(VERSION)},calendar,observations,last)) proof,
        lower(hex(SHA256(proof))) evidence_hash FROM selected
    ) """
    extras=",'regular_price' source_system,concat('listing/',toString(calendar.2),'/',toString(calendar.1)) source_record,calendar.7 source_hash,proof" if wide else ''
    return prefix+f"""SELECT toUUID({literal(dataset_id)}) dataset_id,toUUID({literal(attempt_id)}) attempt_id,
      toUInt8(2) entity_kind,toUInt64({security_key}) entity_key,toUInt32({key('market.regular_close')}) metric_key,
      CAST(NULL AS Nullable(Date)) period_start,toNullable(calendar.1) period_end,toUInt8(1) context_kind,
      toUInt16(0) fiscal_year,'' fiscal_period,toUInt8(0) taxonomy_key,fromUnixTimestamp64Micro(toInt64(calendar.5)) available_at,
      toUInt32(0) revision,if(observations>0 AND last.1<=9007199254740992,toNullable(last.1/10000.),CAST(NULL AS Nullable(Float64))) value,
      toUInt16(5) unit_key,toUInt16(2) basis_unit_key,toUInt8(if(observations>0 AND last.1<=9007199254740992,1,2)) status,
      toUInt8(1) precision,toUInt8(if(observations>0 AND last.1<=9007199254740992,100,0)) confidence,
      multiIf(observations=0,'no_eligible_regular_price',last.1>9007199254740992,'source_integer_exceeds_exact_price_range','') reason,
      evidence_hash{extras} FROM prepared"""

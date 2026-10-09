"""Exact SELECT/producer checks; certified source issuance is an explicit seam."""
import json
from types import SimpleNamespace

import pytest

from test_arte_structural_rejection_exit_v1 import prepared
from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
from src.trading_runtime.structural_rejection_exit_quote_source import load_structural_rejection_exit_quote


def source(monkeypatch):
    _,confirmed,_,context=prepared(monkeypatch)
    witness=confirmed.request.witness
    source=witness.predecessor.source;quote=witness.quote
    row=dict(build_id=source.market_build_id,session_date=source.session_date,ticker=source.ticker,
        attempt_id=source.liquidity_attempt_id,bucket_index=(25000+SESSION_OPEN_OFFSET_MS)//100-1,
        bid_int=quote.bid_int,ask_int=quote.ask_int,quote_valid=1,quote_timestamp_us=quote.observed_at_us)
    queries=[]
    def execute(sql):
        queries.append(sql)
        return json.dumps(row)
    return SimpleNamespace(execute=execute),witness,context.profile,row,queries


def test_exact_pinned_quote_is_verified_by_one_bounded_read(monkeypatch):
    client,witness,profile,row,queries=source(monkeypatch)
    assert load_structural_rejection_exit_quote(client,witness,profile=profile)==witness.quote
    sql,=queries
    assert 'arte.liquidity_100ms_v1' in sql and 'LIMIT 2' in sql
    assert f"attempt_id=toUUID('{row['attempt_id']}')" in sql
    assert 'output_format_json_quote_64bit_integers=0' in sql and 'file(' not in sql
    assert profile.owner.manager.runtime.calls==['entry']


@pytest.mark.parametrize('name',('build_id','session_date','ticker','attempt_id','bucket_index',
    'bid_int','ask_int','quote_valid','quote_timestamp_us'))
def test_source_prices_and_timestamp_cannot_cross_certified_boundaries(monkeypatch,name):
    client,witness,profile,row,_=source(monkeypatch)
    row[name]='foreign' if type(row[name]) is str else row[name]+1
    with pytest.raises(ValueError): load_structural_rejection_exit_quote(client,witness,profile=profile)


@pytest.mark.parametrize('kind',('missing','duplicate','extra-field','bool','float','oversize','nontext'))
def test_ambiguous_or_malformed_quote_reads_fail_closed(monkeypatch,kind):
    client,witness,profile,row,_=source(monkeypatch)
    if kind=='missing': client.execute=lambda sql:''
    elif kind=='duplicate': client.execute=lambda sql:json.dumps(row)+'\n'+json.dumps(row)
    elif kind=='extra-field': row['future']=1
    elif kind=='bool': row['quote_valid']=True
    elif kind=='float': row['bid_int']=float(row['bid_int'])
    elif kind=='oversize': client.execute=lambda sql:' '*262145
    else: client.execute=lambda sql:[]
    with pytest.raises(ValueError): load_structural_rejection_exit_quote(client,witness,profile=profile)

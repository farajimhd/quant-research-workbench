"""Causality, first expansion, identity boundaries and temporal filter selection."""
from dataclasses import replace
import numpy as np
import polars as pl
import pytest
from research.squeeze_filter.v1.analysis import Settings,build_observations,evaluate,discover,dense_bars


def fixture(direction="up"):
    n=40;opens=np.full(n,10.);close=opens.copy();high=opens+.01;low=opens-.01
    # First expansion: candle start300s, close330s, earlier range0.2%.
    high[10]=10.2;low[10]=9.99;close[10]=10.18 if direction=="up" else 9.82
    if direction=="down":low[10]=9.8;high[10]=10.01
    volume=np.full(n,100.);volume[9]=500;volume[10]=9000
    bars=pl.DataFrame(dict(ticker=["X"]*n,time_us=np.arange(1,n+1)*30_000_000,
        open=opens,high=high,low=low,close=close,volume=volume,trades=np.full(n,10),
        notional=volume*10,valid=np.full(n,True)))
    signals=pl.DataFrame(dict(ticker=["X"],symbol_id=["ID"],listing_id=["L"],signal_us=[180_000_000]))
    return bars,signals


def test_first_upward_expansion_has_only_prior_features_and_no_hindsight_range_baseline():
    bars,signals=fixture()
    points,cases=build_observations(bars,signals,day="2026-09-16",start_us=0,end_us=1_200_000_000)
    assert cases["outcome"].to_list()==["up"]
    assert cases["event_us"].to_list()==[300_000_000]
    event=points.filter(pl.col("decision_us")==300_000_000)
    assert event["volume_30s"].item()==500
    assert event["baseline_range"].item()==pytest.approx(.002)
    assert points["decision_us"].max()==300_000_000
    changed=bars.with_columns(pl.when(pl.col("time_us")>330_000_000).then(100000.).otherwise(pl.col("high")).alias("high"))
    new,_=build_observations(changed,signals,day="2026-09-16",start_us=0,end_us=1_200_000_000)
    assert points.equals(new)


def test_downward_first_expansion_is_not_a_good_move_and_unknown_history_is_not_quiet():
    bars,signals=fixture("down")
    _,cases=build_observations(bars,signals,day="D",start_us=0,end_us=1_200_000_000)
    assert cases["outcome"].item()=="down"
    early=signals.with_columns(pl.lit(0).alias("signal_us"))
    points,cases=build_observations(bars,early,day="D",start_us=0,end_us=1_200_000_000)
    assert cases["outcome"].item()=="unknown_history" and points.height==0


def test_rule_acts_once_at_first_pass_and_counts_too_early_selection():
    bars,signals=fixture()
    points,cases=build_observations(bars,signals,day="D",start_us=0,end_us=1_200_000_000)
    assert evaluate(points,cases,[("volume_30s",">=",500)])["true_positives"]==1
    early=evaluate(points,cases,[("volume_30s",">=",100)])
    assert early["selected"]==1 and early["true_positives"]==0 and early["early_selections"]==1
    assert evaluate(points,cases,[("float_shares",">=",0)])["selected"]==0


def test_validation_tail_never_changes_thresholds_or_training_rank():
    points=[];cases=[]
    for day in ("2026-09-16","2026-09-17","2026-09-18"):
        b,s=fixture();x,y=build_observations(b,s,day=day,start_us=0,end_us=1_200_000_000)
        points.append(x);cases.append(y)
    observations=pl.concat(points);cohorts=pl.concat(cases)
    settings=Settings(minimum_passes=1)
    first=discover(observations,cohorts,settings)
    changed=observations.with_columns(pl.when(pl.col("session")=="2026-09-18").then(999999.)
        .otherwise(pl.col("volume_30s")).alias("volume_30s"))
    second=discover(changed,cohorts,settings)
    assert [(r["rule"],r["training"],r["rank"]) for r in first["rules"]]==[(r["rule"],r["training"],r["rank"]) for r in second["rules"]]


def test_duplicate_candles_rejected_and_missing_prices_not_fabricated():
    bars,signals=fixture()
    with pytest.raises(ValueError,match="Duplicate"):
        dense_bars(pl.concat([bars,bars.head(1)]),["X"],0,1_200_000_000)
    absent=dense_bars(bars.slice(1),["X"],0,1_200_000_000).head(1)
    assert absent["volume"].item()==0 and absent["open"].item() is None and not absent["valid"].item()


def test_float_reader_uses_pinned_symbol_id_and_strict_pre_session_availability(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.backend import backtest_market_data
    from research.squeeze_filter.v1.data import floats
    class Reader:
        def execute(self,sql):
            assert "symbol_id IN ('ID')" in sql
            assert "inserted_at<toDateTime64('2026-09-16T08:00:00+00:00'" in sql
            assert "resolution_date<=toDate('2026-09-16')" in sql
            return '{"symbol_id":"ID","float_shares":1000000,"resolution_kind":"resolved","resolution_date":"2026-09-15","available_at":"2026-09-15 12:00:00","source_fingerprint":"1"}'
        def close(self):pass
    monkeypatch.setattr(backtest_market_data,"readonly_clickhouse_client",lambda **kwargs:Reader())
    _,signals=fixture()
    result=floats(signals,datetime(2026,9,16,4,tzinfo=ZoneInfo("America/New_York")))
    assert result["ticker"].item()=="X" and result["float_shares"].item()==1000000


def test_rvol_and_float_features_use_known_prior_denominators():
    bars,signals=fixture()
    context=pl.DataFrame(dict(ticker=["X"],float_shares=[1000000.]))
    history=pl.DataFrame(dict(ticker=["X"]*40,decision_offset=np.arange(40)*30,
        rvol_expected_volume=np.arange(40)*50.,rvol_sessions=[5]*40))
    points,_=build_observations(bars,signals,day="D",start_us=0,end_us=1_200_000_000,context=context,rvol=history)
    event=points.filter(pl.col("decision_us")==300_000_000)
    assert event["session_volume"].item()==1400
    assert event["rvol"].item()==pytest.approx(2.8)
    assert event["float_turnover"].item()==pytest.approx(.0014)


def test_real_launcher_writes_complete_outputs_using_typed_source_fixtures(monkeypatch,tmp_path):
    from research.squeeze_filter.v1 import run_study as launch
    catalog=[dict(day=day,build_id="B",manifest="unused",ledger="unused") for day in
             ("2026-09-16","2026-09-17","2026-09-18")]
    monkeypatch.setattr(launch,"configure_reader",lambda *args:None)
    monkeypatch.setattr(launch,"discover_sources",lambda:dict(sources=catalog))
    def load(source,*args):
        bars,signals=fixture()
        context=pl.DataFrame(dict(ticker=["X"],float_shares=[1000000.]))
        return bars,signals,context,0,1_200_000_000,dict(source_build="B")
    monkeypatch.setattr(launch,"load_session",load)
    monkeypatch.setattr(launch,"rvol_baseline",lambda *args:(None,[]))
    job=launch.main(["--runtime",str(tmp_path),"--plain","--minimum-passes","1"])
    import json
    status=json.loads((job/"status.json").read_text())
    assert status["status"]=="completed" and len(status["completed"])==3
    assert (job/"REPORT.md").is_file() and (job/"thresholds.csv").is_file()
    result=json.loads((job/"thresholds.json").read_text())
    assert result["validation_session"]=="2026-09-18"
    assert result["training_sessions"]==["2026-09-16","2026-09-17"]


def test_watchlist_recovers_only_exact_certified_listing_identity():
    from research.squeeze_filter.v1.data import pinned_signals
    watch=pl.DataFrame(dict(ticker=["X"],listing_id=["L"],admitted_at_us=[1]))
    identities=pl.DataFrame(dict(ticker=["X"],listing_id=["L"],symbol_id=["S"]))
    assert pinned_signals(watch,identities)["symbol_id"].item()=="S"
    with pytest.raises(ValueError,match="certified listing"):
        pinned_signals(watch,identities.with_columns(pl.lit("OTHER").alias("listing_id")))

import itertools
import numpy as np
import polars as pl
import pytest
from research.rl_trading.v6.market_teacher_preview import Config,select,group,apply_decisions,candidate_window

def test_fee_filter_first_eligible_and_price_normalization():
 rows=pl.DataFrame(dict(listing_id=["a","a","b","c"],pair_id=[1,1,1,1],time_us=[1,2,1,1],close=[10.,10.,100.,10.],entry_gain=[.03,.04,.04,.029],action=["ENTRY"]*4))
 pairs=pl.DataFrame(dict(listing_id=["a","b","c"],pair_id=[1]*3,liquidity_accepted=[True]*3,liquidity_rejection_reason=["eligible"]*3))
 out=select(rows,pairs,Config(minimum_return=0))
 assert out.filter(pl.col("selected"))["listing_id"].to_list()==["a","b"]
 assert out.filter(pl.col("listing_id")=="a")["time_us"].item()==2
 assert out.filter(pl.col("listing_id")=="a")["selection_score"].item()>out.filter(pl.col("listing_id")=="b")["selection_score"].item()
 assert select(rows,pairs,Config(minimum_return=.01))["selected"].sum()==0

def test_default_return_floor_rejects_expensive_low_gain_and_keeps_first_pass():
 rows=pl.DataFrame(dict(listing_id=["AAPL","AAPL","cheap"],pair_id=[1,1,1],time_us=[1,2,1],close=[311.,311.,1.],entry_gain=[.116,.4,.02],action=["ENTRY"]*3))
 pairs=pl.DataFrame(dict(listing_id=["AAPL","cheap"],pair_id=[1,1],liquidity_accepted=[True,True],liquidity_rejection_reason=["eligible","eligible"]))
 out=select(rows,pairs,Config())
 assert out.filter(pl.col("selected"))["time_us"].to_list()==[2]
 assert out.filter(pl.col("listing_id")=="cheap")["selected"].item() is False

def test_target_witness_tracks_chosen_entry_and_best_rejected_candidate():
 rows=pl.DataFrame(dict(listing_id=["a","a","b","b"],pair_id=[1]*4,time_us=[1,2,1,2],entry_target_us=[11,22,33,44],close=[100.]*4,entry_gain=[.05,.2,.04,.05],action=["ENTRY"]*4))
 pairs=pl.DataFrame(dict(listing_id=["a","b"],pair_id=[1,1],liquidity_accepted=[True,True],liquidity_rejection_reason=["eligible","eligible"]))
 out=select(rows,pairs,Config())
 assert out.filter(pl.col("listing_id")=="a")["entry_target_us"].item()==22
 assert out.filter(pl.col("listing_id")=="b")["entry_target_us"].item()==44

def test_overlap_chain_atomic_entries_and_touching_endpoints():
 candidates=pl.DataFrame(dict(listing_id=["a","b","c","d","e"],pair_id=[1]*5,time_us=[0,5,5,11,20],entry_target_us=[10,15,9,20,25],selection_score=[1.,2.,1.,4.,3.]))
 out,groups=group(candidates,Config())
 assert out["group_id"].to_list()==[1,1,1,1,2]
 assert out["active_count"].to_list()==[1,3,3,2,1]
 assert out["active_score_sum"].to_list()==pytest.approx([1,4,4,6,3])
 assert out["allocation_ratio"].to_list()==pytest.approx([1,.5,.25,4/6,1])
 assert groups[0]["end_us"]==20 and groups[1]["start_us"]==20
 assert groups[0]["members"]==4

def test_overlap_empty_and_invalid_witness():
 candidates=pl.DataFrame(dict(listing_id=["a"],pair_id=[1],time_us=[0],entry_target_us=[0],selection_score=[1.]))
 with pytest.raises(ValueError):group(candidates,Config())
 out,groups=group(candidates.head(0),Config());assert out.height==0 and not groups
 with pytest.raises(ValueError):Config(grouping_seconds=float("nan")).validate()

def test_liquidity_rejection_and_zero_score_never_get_allocation():
 rows=pl.DataFrame(dict(listing_id=["illiquid","zero"],pair_id=[1,1],time_us=[1,1],close=[10.,10.],entry_gain=[1.,.01],action=["ENTRY","ENTRY"]))
 pairs=pl.DataFrame(dict(listing_id=["illiquid","zero"],pair_id=[1,1],liquidity_accepted=[False,True],liquidity_rejection_reason=["insufficient_trades","eligible"]))
 decisions=select(rows,pairs,Config(threshold_mode="return",minimum_return=0))
 assert not decisions["selected"].any()
 assert decisions["selection_reason"].to_list()==["insufficient_trades","below_score_threshold"]

def test_preview_copy_suppresses_whole_pair_preserving_source_and_context():
 original=dict(labels=[dict(pair_id=pair,action=action,label_value=.9,entry_gain=.04)
  for pair,action in [(1,"ENTRY"),(1,"HOLD"),(1,"EXIT"),(2,"ENTRY"),(2,"EXIT"),(None,"CONTEXT"),(None,"WAIT")]])
 decisions=pl.DataFrame(dict(pair_id=[1,2],selected=[False,True],group_id=[None,3],allocation_ratio=[0.,.4]))
 copied=apply_decisions(original,decisions)
 assert [r["action"] for r in copied["labels"]]==["WAIT","WAIT","WAIT","ENTRY","EXIT","CONTEXT","WAIT"]
 assert [r["action"] for r in original["labels"]]==["ENTRY","HOLD","EXIT","ENTRY","EXIT","CONTEXT","WAIT"]
 assert all(r["entry_gain"]==.04 for r in copied["labels"])
 assert [r["allocation_loss_mask"] for r in copied["labels"]]==[False,False,False,True,False,False,False]
 assert copied["labels"][3]["allocation_ratio"]==.4

def test_positive_second_rows_include_rejected_and_later_candidates():
 rows=pl.DataFrame(dict(listing_id=["a","a","b","b"],pair_id=[1]*4,time_us=[1,2,1,2],close=[100.]*4,entry_gain=[.05,.2,.02,.005],action=["ENTRY"]*4))
 pairs=pl.DataFrame(dict(listing_id=["a","b"],pair_id=[1,1],liquidity_accepted=[True,True],liquidity_rejection_reason=["eligible","eligible"]))
 decisions=select(rows,pairs,Config()).with_columns(pl.col("listing_id").alias("ticker"),pl.when(pl.col("selected")).then(1).otherwise(None).alias("group_id"),pl.when(pl.col("selected")).then(1.).otherwise(0.).alias("allocation_ratio"))
 first=candidate_window(rows,decisions,Config())
 assert first["clocks"]==[1,2] and first["total"]==2
 assert all(r["score"]>0 for r in first["rows"])
 assert not any(r["group_contributor"] for r in first["rows"])
 second=candidate_window(rows,decisions,Config(),2)
 assert second["total"]==1 and second["rows"][0]["group_contributor"]
 rejected=candidate_window(rows,decisions,Config(),selection="rejected")
 assert rejected["total"]==1 and rejected["rows"][0]["ticker"]=="b"

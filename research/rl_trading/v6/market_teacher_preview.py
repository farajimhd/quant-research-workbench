"""Isolated phase-1b supervised preview. Never publishes or starts training."""
from dataclasses import dataclass, asdict
from pathlib import Path
import math
import heapq
import threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import polars as pl
from research.rl_trading.v6 import saved_label_audit as source
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.opportunity_dataset import write_json

VERSION = "rl-v6-market-teacher-preview-v7"
ROOT = Path("D:/TradingML/runtimes/rl-v6-market-teacher-preview")
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teacher-preview")
LOCK = threading.Lock()
JOBS = {}

@dataclass(frozen=True)
class Config:
    fee_per_share: float = .005
    threshold_mode: str = "return"
    minimum_net_fee_multiple: float = 2.
    minimum_return: float = .001
    grouping_seconds: float = 30.
    maximum_group_seconds: int = 300
    def validate(self):
        if self.threshold_mode not in ("fee_multiple", "return"):
            raise ValueError("Unknown score threshold mode")
        if not all(math.isfinite(v) for v in asdict(self).values() if isinstance(v,(float,int))):
            raise ValueError("Nonfinite preview setting")
        if not (0 < self.fee_per_share <= .1 and 0 <= self.minimum_net_fee_multiple <= 100 and 0 <= self.minimum_return <= 1 and 1 <= self.grouping_seconds <= 600 and 1 <= self.maximum_group_seconds <= 3600):
            raise ValueError("Preview setting outside safe bounds")

def select(rows, pairs, config):
    config.validate()
    if "entry_target_us" not in rows.columns:rows=rows.with_columns(pl.lit(None,dtype=pl.Int64).alias("entry_target_us"))
    scored = rows.filter((pl.col("action")=="ENTRY") & (pl.col("pair_id")>0)).with_columns(
        ((pl.col("entry_gain")-2*config.fee_per_share)/(pl.col("close")+config.fee_per_share)).alias("selection_score"))
    cutoff = config.minimum_net_fee_multiple*2*config.fee_per_share/(pl.col("close")+config.fee_per_share)
    scored = scored.with_columns(pl.max_horizontal(cutoff,pl.lit(config.minimum_return)).alias("score_threshold"))
    # Both floors apply: strict fee comparison, inclusive capital-return floor.
    passes = (pl.col("selection_score")>cutoff) & (pl.col("selection_score")>=config.minimum_return)
    eligible = scored.filter(passes & (pl.col("selection_score") > 0)).join(
        pairs.filter(pl.col("liquidity_accepted")).select("listing_id","pair_id"),
        on=["listing_id","pair_id"],how="semi").sort(["listing_id","pair_id","time_us"]).unique(["listing_id","pair_id"],keep="first",maintain_order=True)
    best = scored.group_by("listing_id","pair_id").agg(pl.col("selection_score").max().alias("best_score"),pl.len().alias("entry_rows"))
    auditrows = scored.sort(["listing_id","pair_id","selection_score","time_us"],descending=[False,False,True,False]).unique(["listing_id","pair_id"],keep="first").select("listing_id","pair_id",*[pl.col(k).alias("audit_"+k) for k in ("time_us","close","entry_gain","score_threshold","entry_target_us")])
    result = pairs.join(best,on=["listing_id","pair_id"],how="left",validate="1:1").join(
        eligible.select("listing_id","pair_id","time_us","close","entry_gain","selection_score","score_threshold","entry_target_us"),on=["listing_id","pair_id"],how="left",validate="1:1")
    result = result.join(auditrows,on=["listing_id","pair_id"],how="left",validate="1:1")
    return result.with_columns(pl.col("selection_score").is_not_null().alias("selected"),
        pl.when(~pl.col("liquidity_accepted")).then(pl.col("liquidity_rejection_reason"))
        .when(pl.col("entry_rows").is_null()).then(pl.lit("no_1a_entry"))
        .when(pl.col("selection_score").is_null()).then(pl.lit("below_score_threshold"))
        .otherwise(pl.lit("selected")).alias("selection_reason"),
        *[pl.coalesce(k,"audit_"+k).alias(k) for k in ("time_us","close","entry_gain","score_threshold","entry_target_us")])

def group(candidates, config):
    """Half-open overlap components; sizing compares active scores at each ENTRY."""
    config.validate()
    if candidates.is_empty():
        return candidates.with_columns(pl.lit(None,dtype=pl.Int64).alias("group_id"),pl.lit(0.).alias("allocation_ratio"),pl.lit(0.).alias("active_score_sum"),pl.lit(0,dtype=pl.Int64).alias("active_count")), []
    candidates=candidates.sort("time_us","listing_id","pair_id")
    if candidates.filter(pl.col("entry_target_us").is_null() | (pl.col("entry_target_us")<=pl.col("time_us")) | (pl.col("selection_score")<=0)).height:
        raise ValueError("Overlap grouping requires positive scores and future target witnesses")
    starts=candidates["time_us"].to_list();ends=candidates["entry_target_us"].to_list();scores=candidates["selection_score"].to_list()
    ids=[];ratios=[];denominators=[];counts=[];groups=[];heap=[];active=0.;right=None;number=0;i=0
    while i<len(starts):
        t=starts[i];j=i+1
        while j<len(starts) and starts[j]==t:j+=1
        while heap and heap[0][0]<=t:
            _,_,weight=heapq.heappop(heap);active-=weight
        if right is None or t>=right:
            number+=1;groups.append(dict(group_id=number,start_us=t,end_us=t,duration_seconds=0.,score_sum=0.,members=0));heap=[];active=0.
        batch=sum(scores[i:j]);denominator=active+batch;count=len(heap)+j-i
        for k in range(i,j):
            ids.append(number);ratios.append(scores[k]/denominator);denominators.append(denominator);counts.append(count)
            heapq.heappush(heap,(ends[k],k,scores[k]));groups[-1]["score_sum"]+=scores[k];groups[-1]["members"]+=1
        active=denominator;right=max(groups[-1]["end_us"],max(ends[i:j]))
        groups[-1]["end_us"]=right;groups[-1]["duration_seconds"]=(right-groups[-1]["start_us"])/1e6;i=j
    return candidates.with_columns(pl.Series("group_id",ids,dtype=pl.Int64),pl.Series("allocation_ratio",ratios),pl.Series("active_score_sum",denominators),pl.Series("active_count",counts,dtype=pl.Int64)),groups


def prepare(day, config, dataset_sha256, job):
    try:
        JOBS[job].update(status="running", stage="Checking published 1a bindings")
        active,entry,proof,root,bankroot,_=source.session(day)
        if active["sha256"]!=dataset_sha256:raise ValueError("Published 1a dataset changed")
        input_key=digest(dict(dataset=dataset_sha256,day=day,certificate=entry["teacher_sha256"],projection="positive-gain-rows-v3"))
        ROOT.mkdir(parents=True,exist_ok=True); cache=ROOT/input_key;cache.mkdir(exist_ok=True)
        marker=cache/"complete.json"
        if marker.exists():
            receipt=source.read_json(marker)
            rows=pl.read_parquet(source.verified_local(cache/"candidates.parquet",receipt["candidates_sha256"]))
            pairs=pl.read_parquet(source.verified_local(cache/"pairs.parquet",receipt["pairs_sha256"]))
        else:
            chunks=[];pairchunks=[];counts={};total=0
            for index,shard in enumerate(proof["shards"]):
                JOBS[job].update(stage="Verifying 1a shards",completed=index,total=len(proof["shards"]))
                folder=root/shard["path"];receipt=source.read_json(folder/"complete.json",shard["sha256"])
                if receipt["binding"]!=proof["binding"]:raise ValueError("Preview source binding changed")
                labels=source.verified_local(folder/"labels.parquet",receipt["files"]["labels"]["sha256"])
                frame=pl.scan_parquet(labels)
                count=frame.select(pl.len()).collect().item();total+=count
                if count!=receipt["valid_rows"]:raise ValueError("Preview source row coverage mismatch")
                for row in frame.group_by("action").len().collect().iter_rows():counts[row[0]]=counts.get(row[0],0)+row[1]
                chunks.append(frame.filter(pl.col("entry_gain")>0).select("listing_id","pair_id","time_us","close","entry_gain","entry_target_us","action").collect())
                pairfile=source.verified_local(folder/"pairs.parquet",receipt["files"]["pairs"]["sha256"])
                pairchunks.append(pl.read_parquet(pairfile).select("listing_id","pair_id","start_us","end_us","liquidity_accepted","liquidity_rejection_reason","reference_entry_us","reference_exit_us"))
            if total!=proof["valid_rows"]:raise ValueError("Full session coverage mismatch")
            rows=pl.concat(chunks);pairs=pl.concat(pairchunks)
            rows.write_parquet(cache/"candidates.parquet");pairs.write_parquet(cache/"pairs.parquet")
            write_json(marker,dict(candidates_sha256=file_hash(cache/"candidates.parquet"),pairs_sha256=file_hash(cache/"pairs.parquet"),counts=counts,source_rows=total))
        JOBS[job].update(stage="Scoring and grouping",completed=0,total=1)
        decisions=select(rows,pairs,config);members,groups=group(decisions.filter(pl.col("selected")),config)
        decisions=decisions.join(members.select("listing_id","pair_id","group_id","allocation_ratio","active_score_sum","active_count"),on=["listing_id","pair_id"],how="left",validate="1:1").with_columns(pl.col("allocation_ratio").fill_null(0.))
        names={r["listing_id"]:r["ticker"] for r in source.listings(day)["listings"]}
        decisions=decisions.with_columns(pl.col("listing_id").replace_strict(names).alias("ticker"))
        decisionpath=ROOT/(job+".parquet");decisions.write_parquet(decisionpath)
        receipt=source.read_json(marker)
        changed=rows.filter(pl.col("action")=="ENTRY").join(decisions.filter(~pl.col("selected")).select("listing_id","pair_id"),on=["listing_id","pair_id"],how="semi").height
        result=dict(version=VERSION,day=day,dataset_sha256=dataset_sha256,config=asdict(config),groups=groups,source_rows=receipt["source_rows"],source_actions=receipt["counts"],pairs=pairs.height,selected=members.height,rejected=pairs.height-members.height,suppressed_entry_rows=changed,decisions_path=str(decisionpath),status="preview_only_not_training",objective="Half-open interval overlap components; active-score sizing at ENTRY; no cash simulation",sizing="Ratio target applies only to selected ENTRY rows; HOLD/EXIT sizing loss masked")
        result["decisions_sha256"]=file_hash(decisionpath)
        result["source_input_key"]=input_key
        write_json(ROOT/(job+".json"),result);JOBS[job].update(status="complete",stage="Ready to audit",completed=1,total=1)
    except Exception as error:JOBS[job].update(status="failed",stage=str(error))

def start(day, config):
    config.validate();active,dataset=source.published()
    if day not in [e["day"] for e in [dataset["context"]]+dataset["days"]]:raise ValueError("Session outside approved labels")
    job=digest(dict(version=VERSION,day=day,dataset=active["sha256"],config=asdict(config)))
    with LOCK:
        if JOBS.get(job,{}).get("status")=="failed":JOBS.pop(job)
        if job not in JOBS:
            JOBS[job]=dict(job_id=job,status="queued",stage="Queued",completed=0,total=1)
            if (ROOT/(job+".json")).exists():
                JOBS[job].update(status="complete",stage="Ready to audit",completed=1,total=1)
            else:POOL.submit(prepare,day,config,active["sha256"],job)
    return dict(JOBS[job])

def status(job):
    if job not in JOBS:raise ValueError("Preview job unavailable; prepare again")
    meta=source.read_json(ROOT/(job+'.json')) if (ROOT/(job+'.json')).exists() else {}
    if meta.get('published_1b') and source.read_json(source.runtime()/'rl-v6-active-market-teacher.json')['sha256']!=meta['dataset_sha256']:
        raise ValueError('1b publication changed; reload labels')
    return dict(JOBS[job])

def result(job,group_id=None,search="",offset=0,selection="all"):
    if selection not in ("all","selected","rejected"):raise ValueError("Invalid selection filter")
    if status(job)["status"]!="complete":raise ValueError("Preview not ready")
    meta=source.read_json(ROOT/(job+".json"));frame=pl.read_parquet(source.verified_local(meta.pop("decisions_path"),meta["decisions_sha256"]))
    if selection!="all":frame=frame.filter(pl.col("selected")== (selection=="selected"))
    if group_id is not None:frame=frame.filter(pl.col("group_id")==group_id)
    if search:frame=frame.filter(pl.col("ticker").str.to_uppercase().str.contains(search.upper(),literal=True))
    count=frame.height;frame=frame.sort(["selected","time_us","ticker"],descending=[True,False,False],nulls_last=True)
    return dict(**meta,filtered_rows=count,offset=offset,decisions=frame.slice(offset,100).to_dicts())

def positive_rows(job,time_us=None,search="",selection="all",minimum_score=0.,offset=0):
    """All positive-scored 1a candle rows at one observed 1s close, not just winners."""
    if status(job)["status"]!="complete":raise ValueError("Preview not ready")
    if selection not in ("all","selected","rejected"):raise ValueError("Invalid selection filter")
    meta=source.read_json(ROOT/(job+".json"));cache=ROOT/meta["source_input_key"]
    if meta.get('published_1b'):
        from research.rl_trading.v6.published_market_audit import ensure_candidates
        ensure_candidates(meta)
    receipt=source.read_json(cache/"complete.json")
    rows=pl.read_parquet(source.verified_local(cache/"candidates.parquet",receipt["candidates_sha256"]))
    decisions=pl.read_parquet(source.verified_local(ROOT/(job+".parquet"),meta["decisions_sha256"]))
    return candidate_window(rows,decisions,Config(**meta["config"]),time_us,search,selection,minimum_score,offset)

def timeline(job,start_us=None,seconds=900,search="",include_rejected=False):
    if status(job)["status"]!="complete":raise ValueError("Preview not ready")
    meta=source.read_json(ROOT/(job+".json"))
    frame=pl.read_parquet(source.verified_local(ROOT/(job+".parquet"),meta["decisions_sha256"]))
    positive=frame.filter(pl.coalesce("selection_score","best_score")>0)
    begin=positive["time_us"].min();finish=positive["entry_target_us"].max()
    if begin is None:return dict(start_us=None,end_us=None,begin_us=None,finish_us=None,rows=[],groups=[],total=0,unavailable=0,truncated=False)
    begin=begin//1_000_000//900*900*1_000_000
    start=begin if start_us is None else start_us;end=start+seconds*1_000_000
    if not include_rejected:positive=positive.filter(pl.col("selected"))
    if search:positive=positive.filter(pl.col("ticker").str.to_uppercase().str.contains(search.upper(),literal=True))
    unavailable=positive.filter(pl.col("entry_target_us").is_null() & pl.col("time_us").is_between(start,end,closed="left")).height
    visible=positive.filter((pl.col("time_us")<end)&(pl.col("entry_target_us")>start)).sort("time_us","listing_id","pair_id")
    extents=frame.filter(pl.col("selected")).group_by("group_id").agg(pl.col("entry_target_us").max().alias("target_end_us"))
    targets=dict(extents.iter_rows())
    groups=[dict(g,target_end_us=targets[g["group_id"]]) for g in meta["groups"]
            if g["start_us"]<end and (targets[g["group_id"]] or g["end_us"])>start]
    return dict(start_us=start,end_us=end,begin_us=begin,finish_us=finish,rows=visible.head(5000).to_dicts(),groups=groups,total=visible.height,unavailable=unavailable,truncated=visible.height>5000)

def candidate_window(rows,decisions,config,time_us=None,search="",selection="all",minimum_score=0.,offset=0):
    fee=config.fee_per_share
    frame=rows.with_columns(((pl.col("entry_gain")-2*fee)/(pl.col("close")+fee)).alias("score"))
    frame=frame.filter((pl.col("score")>0)&(pl.col("score")>=minimum_score)).join(
        decisions.select("listing_id","pair_id","ticker","selected","selection_reason","group_id","allocation_ratio",pl.col("time_us").alias("first_eligible_us")),
        on=["listing_id","pair_id"],how="left",validate="m:1")
    if search:frame=frame.filter(pl.col("ticker").str.to_uppercase().str.contains(search.upper(),literal=True))
    if selection!="all":frame=frame.filter(pl.col("selected")== (selection=="selected"))
    clocks=frame["time_us"].unique().sort().to_list()
    if time_us is None:time_us=clocks[0] if clocks else None
    frame=frame.filter(pl.col("time_us")==time_us).with_columns(
        (pl.col("selected")&(pl.col("time_us")==pl.col("first_eligible_us"))).alias("group_contributor"),
        ((pl.col("score")>=config.minimum_return)&(pl.col("entry_gain")>(config.minimum_net_fee_multiple+1)*2*fee)).alias("passes_threshold"))
    frame=frame.sort(["score","ticker","pair_id"],descending=[True,False,False])
    return dict(timeframe="1s",time_us=time_us,clocks=clocks,total=frame.height,offset=offset,rows=frame.slice(offset,100).to_dicts())

def chart(job,listing_id,start_us):
    if status(job)["status"]!="complete":raise ValueError("Preview not ready")
    meta=source.read_json(ROOT/(job+".json"))
    if meta.get('published_1b'):
        from research.rl_trading.v6.published_market_audit import chart as saved_chart
        return saved_chart(meta,listing_id,start_us)
    active,_=source.published()
    if active["sha256"]!=meta["dataset_sha256"]:raise ValueError("1a publication changed")
    decisions=pl.read_parquet(source.verified_local(ROOT/(job+".parquet"),meta["decisions_sha256"])).filter(pl.col("listing_id")==listing_id)
    payload=source.chart(meta["day"],listing_id,start_us,900,"combined",0)
    return apply_decisions(payload,decisions)

def apply_decisions(payload,decisions):
    """Copy 1a chart rows before adding provisional action and sizing targets."""
    payload={**payload,"labels":[dict(row) for row in payload["labels"]]}
    lookup={r["pair_id"]:r for r in decisions.to_dicts()}
    for row in payload["labels"]:
        row["action_1a"]=row["action"];row["label_value_1a"]=row["label_value"];row["allocation_ratio"]=0.;row["allocation_loss_mask"]=False
        decision=lookup.get(row.get("pair_id"))
        if row["action"]=="CONTEXT":continue
        if decision and not decision["selected"]:row.update(action="WAIT",label_value=1.)
        if decision and decision["selected"] and row["action"]=="ENTRY":
            row.update(allocation_ratio=decision["allocation_ratio"],allocation_loss_mask=True)
        row["group_id"]=decision["group_id"] if decision else None
    payload["preview_version"]=VERSION
    return payload


def competitors(job,listing_id,pair_id):
    if status(job)["status"]!="complete":raise ValueError("Preview not ready")
    meta=source.read_json(ROOT/(job+".json"));frame=pl.read_parquet(source.verified_local(ROOT/(job+".parquet"),meta["decisions_sha256"]))
    chosen=frame.filter((pl.col("listing_id")==listing_id)&(pl.col("pair_id")==pair_id))
    if chosen.height!=1:raise ValueError("Unknown episode")
    row=chosen.row(0,named=True)
    if not row["selected"]:return dict(time_us=row["time_us"],active_score_sum=0.,rows=[])
    t=row["time_us"]
    active=frame.filter(pl.col("selected")&(pl.col("time_us")<=t)&(pl.col("entry_target_us")>t)).sort("time_us","listing_id","pair_id")
    return dict(time_us=t,active_score_sum=active["selection_score"].sum(),rows=active.select("listing_id","ticker","pair_id","time_us","entry_target_us","selection_score").to_dicts())

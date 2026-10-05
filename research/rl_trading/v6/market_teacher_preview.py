"""Isolated phase-1b supervised preview. Never publishes or starts training."""
from dataclasses import dataclass, asdict
from pathlib import Path
import math
import threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import polars as pl
from research.rl_trading.v6 import saved_label_audit as source
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.opportunity_dataset import write_json

VERSION = "rl-v6-market-teacher-preview-v4"
ROOT = Path("D:/TradingML/runtimes/rl-v6-market-teacher-preview")
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teacher-preview")
LOCK = threading.Lock()
JOBS = {}

@dataclass(frozen=True)
class Config:
    fee_per_share: float = .005
    threshold_mode: str = "fee_multiple"
    minimum_net_fee_multiple: float = 2.
    minimum_return: float = .01
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
    scored = rows.filter((pl.col("action")=="ENTRY") & (pl.col("pair_id")>0)).with_columns(
        ((pl.col("entry_gain")-2*config.fee_per_share)/(pl.col("close")+config.fee_per_share)).alias("selection_score"))
    cutoff = config.minimum_net_fee_multiple*2*config.fee_per_share/(pl.col("close")+config.fee_per_share)
    scored = scored.with_columns((cutoff if config.threshold_mode=="fee_multiple" else pl.lit(config.minimum_return)).alias("score_threshold"))
    # Strict > matches the proposed gain > 6f contract; old return mode retains >=.
    passes = pl.col("selection_score")>pl.col("score_threshold") if config.threshold_mode=="fee_multiple" else pl.col("selection_score")>=pl.col("score_threshold")
    eligible = scored.filter(passes & (pl.col("selection_score") > 0)).join(
        pairs.filter(pl.col("liquidity_accepted")).select("listing_id","pair_id"),
        on=["listing_id","pair_id"],how="semi").sort(["listing_id","pair_id","time_us"]).unique(["listing_id","pair_id"],keep="first",maintain_order=True)
    best = scored.group_by("listing_id","pair_id").agg(pl.col("selection_score").max().alias("best_score"),pl.len().alias("entry_rows"))
    auditrows = scored.sort(["listing_id","pair_id","selection_score","time_us"],descending=[False,False,True,False]).unique(["listing_id","pair_id"],keep="first").select("listing_id","pair_id",*[pl.col(k).alias("audit_"+k) for k in ("time_us","close","entry_gain","score_threshold")])
    result = pairs.join(best,on=["listing_id","pair_id"],how="left",validate="1:1").join(
        eligible.select("listing_id","pair_id","time_us","close","entry_gain","selection_score","score_threshold"),on=["listing_id","pair_id"],how="left",validate="1:1")
    result = result.join(auditrows,on=["listing_id","pair_id"],how="left",validate="1:1")
    return result.with_columns(pl.col("selection_score").is_not_null().alias("selected"),
        pl.when(~pl.col("liquidity_accepted")).then(pl.col("liquidity_rejection_reason"))
        .when(pl.col("entry_rows").is_null()).then(pl.lit("no_1a_entry"))
        .when(pl.col("selection_score").is_null()).then(pl.lit("below_score_threshold"))
        .otherwise(pl.lit("selected")).alias("selection_reason"),
        *[pl.coalesce(k,"audit_"+k).alias(k) for k in ("time_us","close","entry_gain","score_threshold")])

def group(candidates, config):
    """Exact penalized ordered segmentation within the explicitly bounded span.

    Atomic equal-time members cannot be split. Cost is score-weighted temporal
    SSE plus median candidate score * grouping_seconds**2 per group.
    """
    if candidates.is_empty():
        return candidates.with_columns(pl.lit(None,dtype=pl.Int64).alias("group_id"),pl.lit(0.).alias("allocation_ratio")), []
    atoms=candidates.group_by("time_us").agg(pl.col("selection_score").sum().alias("weight")).sort("time_us")
    clock=atoms["time_us"].to_numpy(); t=(clock-clock[0])/1e6; w=atoms["weight"].to_numpy()
    prefix=[np.r_[0,np.cumsum(x)] for x in (w,w*t,w*t*t)]
    penalty=float(candidates["selection_score"].median())*config.grouping_seconds**2
    n=len(t); dp=np.full(n+1,np.inf); dp[0]=0; prev=np.zeros(n+1,dtype=np.int64)
    for right in range(1,n+1):
        left=np.arange(np.searchsorted(t,t[right-1]-config.maximum_group_seconds),right)
        a,b,c=[v[right]-v[left] for v in prefix]
        costs=dp[left]+np.maximum(0,c-b*b/a)+penalty
        choice=int(np.argmin(costs));dp[right]=costs[choice];prev[right]=left[choice]
    spans=[];r=n
    while r: spans.append((int(prev[r]),r));r=int(prev[r])
    spans.reverse();ids=np.empty(n,dtype=np.int64);summaries=[]
    for number,(l,r) in enumerate(spans,1):
        ids[l:r]=number;summaries.append(dict(group_id=number,start_us=int(clock[l]),end_us=int(clock[r-1]),duration_seconds=float(t[r-1]-t[l]),score_sum=float(w[l:r].sum()),objective_penalty=penalty))
    members=candidates.join(pl.DataFrame(dict(time_us=clock,group_id=ids)),on="time_us",validate="m:1").with_columns(
        (pl.col("selection_score")/pl.col("selection_score").sum().over("group_id")).alias("allocation_ratio"))
    totals=members.group_by("group_id").agg(pl.col("allocation_ratio").sum())
    if not np.allclose(totals["allocation_ratio"].to_numpy(),1,rtol=0,atol=1e-12):raise ValueError("Allocation ratios do not conserve group weight")
    counts=dict(members.group_by("group_id").len().iter_rows())
    for item in summaries:item["members"]=counts[item["group_id"]]
    return members,summaries

def prepare(day, config, dataset_sha256, job):
    try:
        JOBS[job].update(status="running", stage="Checking published 1a bindings")
        active,entry,proof,root,bankroot,_=source.session(day)
        if active["sha256"]!=dataset_sha256:raise ValueError("Published 1a dataset changed")
        input_key=digest(dict(dataset=dataset_sha256,day=day,certificate=entry["teacher_sha256"]))
        ROOT.mkdir(parents=True,exist_ok=True); cache=ROOT/input_key;cache.mkdir(exist_ok=True)
        marker=cache/"complete.json"
        if marker.exists():
            receipt=source.read_json(marker)
            rows=pl.read_parquet(source.verified_local(cache/"entries.parquet",receipt["entries_sha256"]))
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
                chunks.append(frame.filter(pl.col("action")=="ENTRY").select("listing_id","pair_id","time_us","close","entry_gain","action").collect())
                pairfile=source.verified_local(folder/"pairs.parquet",receipt["files"]["pairs"]["sha256"])
                pairchunks.append(pl.read_parquet(pairfile).select("listing_id","pair_id","start_us","end_us","liquidity_accepted","liquidity_rejection_reason","reference_entry_us","reference_exit_us"))
            if total!=proof["valid_rows"]:raise ValueError("Full session coverage mismatch")
            rows=pl.concat(chunks);pairs=pl.concat(pairchunks)
            rows.write_parquet(cache/"entries.parquet");pairs.write_parquet(cache/"pairs.parquet")
            write_json(marker,dict(entries_sha256=file_hash(cache/"entries.parquet"),pairs_sha256=file_hash(cache/"pairs.parquet"),counts=counts,source_rows=total))
        JOBS[job].update(stage="Scoring and grouping",completed=0,total=1)
        decisions=select(rows,pairs,config);members,groups=group(decisions.filter(pl.col("selected")),config)
        decisions=decisions.join(members.select("listing_id","pair_id","group_id","allocation_ratio"),on=["listing_id","pair_id"],how="left",validate="1:1").with_columns(pl.col("allocation_ratio").fill_null(0.))
        names={r["listing_id"]:r["ticker"] for r in source.listings(day)["listings"]}
        decisions=decisions.with_columns(pl.col("listing_id").replace_strict(names).alias("ticker"))
        decisionpath=ROOT/(job+".parquet");decisions.write_parquet(decisionpath)
        receipt=source.read_json(marker)
        changed=rows.join(decisions.filter(~pl.col("selected")).select("listing_id","pair_id"),on=["listing_id","pair_id"],how="semi").height
        result=dict(version=VERSION,day=day,dataset_sha256=dataset_sha256,config=asdict(config),groups=groups,source_rows=receipt["source_rows"],source_actions=receipt["counts"],pairs=pairs.height,selected=members.height,rejected=pairs.height-members.height,suppressed_entry_rows=changed,decisions_path=str(decisionpath),status="preview_only_not_training",objective="Weighted timestamp spread plus group penalty; no portfolio P&L or cash simulation",sizing="Ratio target applies only to selected ENTRY rows; HOLD/EXIT sizing loss masked")
        result["decisions_sha256"]=file_hash(decisionpath)
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

def chart(job,listing_id,start_us):
    if status(job)["status"]!="complete":raise ValueError("Preview not ready")
    meta=source.read_json(ROOT/(job+".json"))
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

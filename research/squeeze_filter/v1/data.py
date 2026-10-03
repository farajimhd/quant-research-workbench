"""Certified ARTE candles and Reference Gateway point-in-time float, SELECT only."""
from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import polars as pl

from research.vectorized_backtest.v2.torch_backtest.availability import session_bounds
from research.vectorized_backtest.v2.torch_backtest.encoding.config import Session,Funnel
from research.vectorized_backtest.v2.torch_backtest.encoding.catalog import arte_catalog
from research.vectorized_backtest.v2.torch_backtest.encoding.clickhouse import (
    prepare_session,certify_source,_validate_units,_scope)
from research.vectorized_backtest.v2.torch_backtest.source import arte_source,arte_sql
from research.vectorized_backtest.v2.torch_backtest.runtime import write_json,file_hash


NY=ZoneInfo("America/New_York")


def bounds(day,kind):
    start,end=session_bounds(day,kind)
    return (datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=NY),
            datetime.fromisoformat(f"{day}T{end}").replace(tzinfo=NY))


def floats(signals,start):
    from src.backend.backtest_market_data import readonly_clickhouse_client
    result=[];cutoff=start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
        for offset in range(0,signals.height,256):
            group=signals.slice(offset,256)
            names=",".join(arte_sql.literal(s) for s in group["symbol_id"])
            statement=("SELECT symbol_id,tupleElement(f,1) AS float_shares,"
                "tupleElement(f,2) AS resolution_kind,tupleElement(f,3) AS resolution_date,"
                "tupleElement(f,4) AS available_at,tupleElement(f,5) AS source_fingerprint FROM "
                "(SELECT symbol_id,argMax(tuple(float_shares,resolution_kind,toString(resolution_date),"
                "toString(inserted_at),toString(source_fingerprint)),tuple(resolution_date,inserted_at)) AS f "
                "FROM q_live.market_security_float_resolved_v1 FINAL "
                f"WHERE symbol_id IN ({names}) AND resolution_date<=toDate({arte_sql.literal(start.date())}) "
                f"AND inserted_at<toDateTime64({arte_sql.literal(cutoff)},3,'UTC') GROUP BY symbol_id) FORMAT JSONEachRow")
            result.extend(json.loads(line) for line in reader.execute(statement).splitlines() if line)
    if result:
        raw=pl.DataFrame(result,schema_overrides={"symbol_id":pl.String,"float_shares":pl.Float64})
    else:
        raw=pl.DataFrame(schema={"symbol_id":pl.String,"float_shares":pl.Float64,
            "resolution_kind":pl.String,"resolution_date":pl.String,"available_at":pl.String,"source_fingerprint":pl.String})
    joined=signals.select("ticker","symbol_id").join(raw,on="symbol_id",how="left",validate="1:1")
    if joined.filter(pl.col("float_shares").is_not_null() &
                     (~pl.col("float_shares").is_finite() | (pl.col("float_shares")<=0))).height:
        raise ValueError("Resolved point-in-time float contains invalid shares")
    return joined


def pinned_signals(watchlist,members):
    identities=members.select("ticker",pl.col("listing_id").cast(pl.String),"symbol_id")
    signals=watchlist.join(identities,on=["ticker","listing_id"],how="left",validate="1:1")
    if signals["symbol_id"].null_count():
        raise ValueError("Squeeze watchlist differs from certified listing identity")
    return signals.select("ticker","symbol_id","listing_id",pl.col("admitted_at_us").alias("signal_us"))


def load_session(source,kind,runtime,progress):
    start,end=bounds(source["day"],kind)
    session=Session(Path(source["manifest"]),Path(source["ledger"]),runtime/"source-cache",
        start,end,strategy_ms=30000,broker_ms=30000,warmup_seconds=57600,max_prepared_gib=2.)
    fields={"open","high","low","close","volume","trade_count","notional"}
    dependencies=tuple(x for x in arte_catalog().inputs if x.resolution_ms==30000
                       and x.source=="bars" and x.name.split("@")[0] in fields)
    # Same released early-squeeze admission/greedy episode and pinned identities
    # as GPU v2. No V7, broker simulation, strategy registration or financial run.
    prepared=prepare_session(session,Funnel(),dependencies,progress=progress)
    frame=prepared.features[30000].join(prepared.watchlist.select("listing_id","ticker"),
                                      on="listing_id",validate="m:1")
    bars=frame.select("ticker","time_us",
        *[(pl.col(f"{key}_int_30000")/10000).alias(key) for key in ("open","high","low","close")],
        pl.col("volume_30000").alias("volume"),pl.col("trade_count_30000").alias("trades"),
        pl.col("notional_30000").alias("notional"),
        ((pl.col("price_valid_30000")==1)&(pl.col("extremes_valid_30000")==1)).alias("valid"))
    start_us,end_us=int(start.timestamp()*1_000_000),int(end.timestamp()*1_000_000)
    bars=bars.filter((pl.col("time_us")>start_us)&(pl.col("time_us")<=end_us))
    proof=certify_source(session)
    with closing(arte_sql.ArteReader()) as reader:
        members,population=arte_source.population(reader,proof.source,start.date(),
                                                  excluded_tickers=session.excluded_tickers)
    signals=pinned_signals(prepared.watchlist,pl.DataFrame(members))
    context=floats(signals,start) if signals.height else pl.DataFrame()
    return bars,signals,context,start_us,end_us,dict(source_key=prepared.source_key,
        source_build=source["build_id"],manifest_sha256=file_hash(session.manifest),
        metrics=prepared.metrics,float_authority="q_live.market_security_float_resolved_v1",
        float_cutoff=start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S"))


def rvol_baseline(target_source,kind,signals,catalog,settings,runtime,progress):
    prior=[s for s in catalog if s["day"]<target_source["day"]][-settings.prior_sessions:]
    target_start,target_end=bounds(target_source["day"],kind)
    duration=int((target_end-target_start).total_seconds())
    days=[];receipts=[]
    for index,source in enumerate(prior):
        progress(dict(stage="RVOL history",completed=index,total=len(prior),message=source["day"]))
        start,end=bounds(source["day"],kind)
        config=Session(Path(source["manifest"]),Path(source["ledger"]),runtime/"rvol-cache",start,end,
                       strategy_ms=30000,broker_ms=30000)
        proof=certify_source(config);day=start.date()
        with closing(arte_sql.ArteReader()) as reader:
            arte_source.storage_check(reader)
            members,population=arte_source.population(reader,proof.source,day,excluded_tickers=("LGHL",))
            identities=pl.DataFrame(members,schema_overrides={"ticker":pl.String,"symbol_id":pl.String})
            stable=signals.select("ticker","symbol_id").join(identities.select("ticker","symbol_id"),
                on=["ticker","symbol_id"],how="inner",validate="1:1")
            names=sorted(stable["ticker"].to_list())
            if not names:
                receipts.append(dict(day=source["day"],stable_listings=0));continue
            signature=sha256(json.dumps(dict(day=source["day"],build=source["build_id"],
                names=names,population=population,kind=kind,version=1,
                manifest_sha256=proof.manifest_hash),sort_keys=True,default=str).encode()).hexdigest()
            folder=runtime/"rvol-cache"/signature;folder.mkdir(parents=True,exist_ok=True)
            path=folder/"volumes.parquet";seal=folder/"complete.json"
            if seal.exists():
                receipt=json.loads(seal.read_text())
                if receipt["signature"]!=signature or file_hash(path)!=receipt["sha256"]:
                    raise ValueError("Corrupt RVOL cache")
                dense=pl.read_parquet(path)
            else:
                _validate_units(reader,proof.source,day,names,None)
                origin=int(datetime.combine(day,datetime.min.time(),NY).timestamp()*1_000_000)
                lo,hi=int(start.timestamp()*1_000_000),int(end.timestamp()*1_000_000)
                chunks=[]
                for offset in range(0,len(names),64):
                    scope=_scope(proof.source,day,names[offset:offset+64],"bars")
                    statement=(f"SELECT ticker,toInt64({origin})+(toInt64(bucket_index)+1)*30000000 AS time_us,"
                        f"volume FROM arte.bars_v1 WHERE {scope} AND resolution_ms=30000 "
                        f"AND bucket_index>={(lo-origin)//30000000} AND bucket_index<{(hi-origin)//30000000} ORDER BY ticker,bucket_index")
                    chunks.append(arte_source.frame(reader,statement,{"ticker":pl.String,"time_us":pl.Int64,"volume":pl.Float64}))
                sparse=pl.concat(chunks)
                # Complete sparse-source certification permits zero volume at
                # absent buckets. Historical price candles are never fabricated.
                grid=pl.DataFrame({"ticker":names}).join(pl.DataFrame({"time_us":np.arange(lo+30_000_000,hi+1,30_000_000)}),how="cross")
                dense=grid.join(sparse,on=["ticker","time_us"],how="left",validate="1:1").sort("ticker","time_us").with_columns(
                    pl.col("volume").fill_null(0).cum_sum().over("ticker").alias("cumulative_volume"),
                    ((pl.col("time_us")-lo)//1_000_000).alias("decision_offset"))
                dense=dense.select("ticker","decision_offset","cumulative_volume")
                dense.write_parquet(path)
                write_json(seal,dict(signature=signature,sha256=file_hash(path),source=source))
            days.append(dense.filter(pl.col("decision_offset")<=duration).with_columns(pl.lit(source["day"]).alias("prior_session")))
            receipts.append(dict(day=source["day"],build_id=source["build_id"],manifest_sha256=proof.manifest_hash,
                                 stable_listings=len(names),snapshot_hash=population["snapshot_hash"]))
    progress(dict(stage="RVOL history",completed=len(prior),total=len(prior),message="Prior-session baseline verified"))
    if not days:
        return pl.DataFrame(schema={"ticker":pl.String,"decision_offset":pl.Int64,"rvol_expected_volume":pl.Float64,"rvol_sessions":pl.UInt32}),receipts
    return pl.concat(days).group_by("ticker","decision_offset").agg(
        pl.col("cumulative_volume").mean().alias("rvol_expected_volume"),
        pl.col("prior_session").n_unique().alias("rvol_sessions")),receipts

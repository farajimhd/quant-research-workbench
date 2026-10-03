"""Study existing ARTE 30s candles; default: latest three premarket sessions."""
import os
import sys
from pathlib import Path
os.environ["PYTHONDONTWRITEBYTECODE"]="1"
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[3]
if __package__ in (None,""):sys.path.insert(0,str(ROOT))

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from time import monotonic
from uuid import uuid4
import subprocess
import polars as pl
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from research.squeeze_filter.v1.analysis import Settings,FEATURES,build_observations,discover
from research.squeeze_filter.v1.data import load_session,rvol_baseline
from research.vectorized_backtest.v2.torch_backtest.availability import configure_reader,discover_sources,WINDOWS
from research.vectorized_backtest.v2.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v2.torch_backtest.progress import safe_diagnostic

DEFAULT=Path("D:/TradingML/runtimes/squeeze_filter/v1")


class Progress:
    def __init__(self,path,total,plain=False):
        self.path=path;self.total=total;self.completed=0;self.started=monotonic()
        self.stage="Source catalogue";self.focus="";self.message="";self.failed=0
        self.phase="";self.status="RUNNING"
        self.console=Console(no_color=bool(os.environ.get("NO_COLOR")))
        self.plain=plain or not self.console.is_terminal
        self.live=None;self.last=0
    def __enter__(self):
        if not self.plain:self.live=Live(self.render(),console=self.console,auto_refresh=False);self.live.start()
        return self
    def render(self):
        remaining=max(0,self.total-self.completed)
        return Panel(Text(f"{self.status} | {self.stage}\n{self.focus}\n"
            f"Sessions saved {self.completed}/{self.total} | active {int(self.status=='RUNNING')} | queued {max(0,remaining-1)} | failed {self.failed}\n"
            f"{self.phase}\nElapsed {monotonic()-self.started:.0f}s | {self.message}"),title="Squeeze filter discovery")
    def emit(self,value):
        self.stage=value.get("stage",self.stage);self.message=value.get("message",self.message)
        if "completed" in value and "total" in value:self.phase=f"Stage units {value['completed']}/{value['total']}"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(dict(elapsed_seconds=monotonic()-self.started,**value),default=str)+"\n")
        if self.plain:
            if monotonic()-self.last>=5 or ("completed" in value and value.get("completed")==value.get("total")):
                self.console.print(f"{self.stage} | {self.focus} | saved {self.completed}/{self.total} | {self.phase} | {self.message}",markup=False)
                self.last=monotonic()
        else:self.live.update(self.render(),refresh=True)
    def __exit__(self,*args):
        if self.live:self.live.update(self.render(),refresh=True);self.live.stop()


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dates",nargs="+",help="Explicit discovery dates YYYY-MM-DD; latest date is temporal validation")
    p.add_argument("--n-sessions",type=int,default=3,help="Latest N available sessions when dates omitted")
    p.add_argument("--session",choices=tuple(WINDOWS),default="premarket")
    p.add_argument("--runtime",type=Path,default=DEFAULT)
    p.add_argument("--baseline-bars",type=int,default=20)
    p.add_argument("--minimum-history",type=int,default=5)
    p.add_argument("--range-multiplier",type=float,default=2.)
    p.add_argument("--minimum-range",type=float,default=.005)
    p.add_argument("--upward-close-location",type=float,default=.75)
    p.add_argument("--prediction-seconds",type=int,default=30)
    p.add_argument("--prior-sessions",type=int,default=13)
    p.add_argument("--minimum-rvol-sessions",type=int,default=5)
    p.add_argument("--minimum-passes",type=int,default=10)
    p.add_argument("--plan",action="store_true",help="Show exact sessions/settings without fetching study data")
    p.add_argument("--plain",action="store_true")
    return p


def write_report(job,result,cases,observations,coverage):
    lines=["# Exploratory squeeze filter study","",f"Training: {', '.join(result['training_sessions'])}; temporal validation: {result['validation_session']}.",
        "Existing ARTE 30-second candles; first unusual candle after the early signal. Up/down/two-sided outcomes are separate.",
        "Thresholds and paired rules are selected using training sessions only. The final date is an initial validation slice, not proof of generalization.",
        f"Recommendation status: {result['recommendation_status']}.",
        "","| Rank | Prior feature rule | Train selected | Train precision | Train recall | Validation selected | Validation precision | Validation recall |",
        "|---:|---|---:|---:|---:|---:|---:|---:|"]
    outcome_counts=cases.group_by("session","outcome").len().sort("session","outcome").to_dicts()
    known=cases.filter(~pl.col("outcome").is_in(["unknown_history","no_post_signal_prices"])).height
    summary=[f"Observable cohorts: {known}/{cases.height}; unknown history and missing prices are excluded, never quiet negatives."]
    candidate=result["recommended_rule"]
    if candidate:
        validation=candidate["validation"]
        summary.append(f"Frozen training candidate validation: {validation['true_positives']}/{validation['upward_events']} upward events caught, {validation['selected']} selections. No adoption or profitability claim.")
    summary.extend(["", "Outcome coverage:", "```json",json.dumps(outcome_counts,indent=2),"```",""])
    lines[6:6]=summary
    for row in result["rules"][:20]:
        rule=" AND ".join(f"{f} {op} {v:.6g}" for f,op,v in row["rule"]) or "Unfiltered baseline"
        a,b=row["training"],row["validation"]
        lines.append(f"| {row['rank']} | {rule} | {a['selected']} | {a['precision']:.1%} | {a['recall']:.1%} | {b['selected']} | {b['precision']:.1%} | {b['recall']:.1%} |")
    lines.extend(["","Each rule acts at its **first qualifying decision point** per ticker/session, before the next candle starts. A success requires the first upward expansion within the declared prediction window. Early passes, downward/two-sided moves and quiet signals are exposed.",
        "","A candle is unusual when its normalized range is at least max(minimum_range, range_multiplier × median of earlier valid ranges). No full-session range statistic selects the event.",
        "","RVOL compares session-to-date volume with the same session offset on earlier certified sessions, requiring the same pinned symbol identity. Float uses resolved values inserted before the session, matched by symbol ID. Missing or zero-denominator inputs cannot pass thresholds; coverage is explicit.",
        "","Session change and later price observations are descriptive outcomes only. No strategy code, grid, broker simulation or production thresholds were changed.","","Feature coverage:","```json",json.dumps(coverage,indent=2),"```"])
    (job/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main(argv=None):
    args=parser().parse_args(argv)
    settings=Settings(**{key:getattr(args,key) for key in asdict(Settings())}).validate()
    if not 2<=args.n_sessions<=20:raise ValueError("Discovery must be bounded to 2-20 sessions")
    runtime=require_runtime(args.runtime)
    configure_reader(ROOT)
    catalog=discover_sources()["sources"]
    by_date={source["day"]:source for source in catalog}
    selected=sorted(set(args.dates)) if args.dates else sorted(by_date)[-args.n_sessions:]
    if args.dates and len(selected)!=len(args.dates):raise ValueError("Duplicate discovery dates")
    if len(selected)<2 or len(selected)>20 or set(selected)-set(by_date):
        raise ValueError("Require 2-20 available catalogue dates; no missing dates skipped")
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip() if (ROOT/".git").exists() else ""
    marker=ROOT/"study-deployment.json"
    if not commit and marker.is_file():
        receipt=json.loads(marker.read_text())
        for relative,digest in receipt["files"].items():
            if file_hash(ROOT/relative)!=digest:raise ValueError("Study deployment source hash differs")
        commit=receipt["commit"]
    if not commit:raise ValueError("Require a committed source checkout or verified study deployment")
    code_hash=sha256(b"".join(p.name.encode()+p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()
    request=dict(version=1,commit=commit,implementation_sha256=code_hash,settings=asdict(settings),
                 session=args.session,dates=selected,population="Pinned preopen tradables, explicit LGHL exclusion",
                 sources=[by_date[day] for day in selected])
    print(json.dumps(request,indent=2),flush=True)
    if args.plan:return request
    job=require_runtime(runtime/"jobs"/uuid4().hex);write_json(job/"request.json",request)
    status=dict(status="active",completed=[],failed=[],queued=list(selected),output=str(job))
    write_json(job/"status.json",status)
    with Progress(job/"progress.jsonl",len(selected),args.plain) as ui:
        try:
            observations=[];cases=[];receipts=[]
            for day in selected:
                ui.focus=f"{day} {args.session}"
                bars,signals,context,start,end,receipt=load_session(by_date[day],args.session,runtime,ui.emit)
                if signals.height==0:raise ValueError(f"{day}: no squeeze signals; empty sessions cannot disappear from study")
                history,prior_receipts=rvol_baseline(by_date[day],args.session,signals,catalog,settings,runtime,ui.emit)
                ui.emit(dict(stage="Causal features and first expansion",message=f"{signals.height} signal tickers"))
                points,cohorts=build_observations(bars,signals,day=day,start_us=start,end_us=end,
                                                settings=settings,context=context,rvol=history)
                daily=require_runtime(job/day);points.write_parquet(daily/"observations.parquet")
                cohorts.write_parquet(daily/"signals-and-events.parquet");context.write_parquet(daily/"float-asof.parquet")
                receipt.update(prior_sessions=prior_receipts,files={p.name:file_hash(p) for p in daily.glob("*.parquet")})
                write_json(daily/"source-receipt.json",receipt)
                receipts.append(receipt);observations.append(points);cases.append(cohorts)
                ui.completed+=1;status["completed"].append(day);status["queued"].remove(day);write_json(job/"status.json",status)
                ui.emit(dict(stage="Session saved",message=f"{cohorts.height} cohorts; {points.height} prior-feature decisions"))
            points=pl.concat(observations,how="vertical_relaxed");cohorts=pl.concat(cases,how="vertical_relaxed")
            ui.emit(dict(stage="Threshold discovery",message="Earlier dates train; last date validates frozen first-pass rules"))
            result=discover(points,cohorts,settings)
            coverage={day:{feature:dict(nonnull=int(points.filter(pl.col("session")==day)[feature].is_not_null().sum()),
                total=points.filter(pl.col("session")==day).height) for feature in FEATURES} for day in selected}
            result.update(coverage=coverage,outcomes=cohorts.group_by("session","outcome").len().to_dicts())
            write_json(job/"thresholds.json",result)
            points.write_parquet(job/"observations.parquet");cohorts.write_parquet(job/"signals-and-events.parquet")
            pl.DataFrame([dict(rank=r["rank"],rule=json.dumps(r["rule"]),
                **{"train_"+k:v for k,v in r["training"].items()},
                **{"validation_"+k:v for k,v in r["validation"].items()}) for r in result["rules"]]).write_csv(job/"thresholds.csv")
            write_report(job,result,cohorts,points,coverage)
            status.update(status="completed",files={p.name:file_hash(p) for p in job.iterdir()
                if p.is_file() and p.name not in ("status.json","progress.jsonl")})
            write_json(job/"status.json",status);ui.status="COMPLETED"
            ui.emit(dict(stage="Results saved",message=str(job/"REPORT.md")))
            print(f"Study completed | sessions {len(selected)} | report {job/'REPORT.md'}",flush=True)
            return job
        except BaseException as error:
            status.update(status="interrupted" if isinstance(error,KeyboardInterrupt) else "failed",error=safe_diagnostic(error))
            if ui.focus:status["failed"].append(ui.focus)
            write_json(job/"status.json",status);ui.failed=int(status["status"]=="failed");ui.status=status["status"].upper()
            ui.emit(dict(stage=ui.status,message=status["error"]))
            raise


if __name__=="__main__":main()

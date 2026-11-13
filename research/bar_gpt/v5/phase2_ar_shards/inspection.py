"""Read-only Research adapter for session-scoped 4096-origin shards."""

import json
from pathlib import Path, PureWindowsPath
import random
import re

import numpy as np

from ..shard_inspection import singleflight_cache
from ..shards.contract import digest
from ..shards.sql import clock
from ..quote_repair_v1 import verify_seal
from .contract import CHANNELS, FRAMES, BLOCK, VERSION
from .storage import ShardReader, read_receipt

ROOT = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\bar_gpt\phase2-ar-overlap-campaign-v1")


def _root():
    """Follow the campaign pointer; never fall back to a retired dataset."""
    pointer = ROOT / "campaign.json"
    if not pointer.exists():
        return ROOT  # Explicit standalone root (also used by isolated tests).
    selected = PureWindowsPath(json.loads(pointer.read_text())["active_root"])
    if (selected.parent != PureWindowsPath('D:/TradingML/runtimes/bar_gpt')
        and selected.parent != PureWindowsPath(str(ROOT.parent))):
        raise ValueError("Foreign phase-two campaign root")
    if not re.fullmatch(r'phase2-ar-sampled-v2-overlap-\d{4}', selected.name):
        raise ValueError("Foreign phase-two campaign root")
    active = ROOT.parent / selected.name
    _plan(str(active))  # Seal and contract validation before reading its index.
    return active


@singleflight_cache(maxsize=2)
def _plan(root):
    value = json.loads((Path(root) / "plan.json").read_text())
    verify_seal(value, "hash")
    if value["version"] != VERSION:
        raise ValueError("Unsupported phase-two shard contract")
    return value


def _catalog():
    plan = _plan(str(_root()))
    path = _root() / "research-catalog.json"
    value = json.loads(path.read_text()) if path.exists() else dict(binding=plan["hash"],streams=[])
    if path.exists():
        verify_seal(value, "hash")
    if value["binding"] != plan["hash"]:
        raise ValueError("Research index belongs to another source plan")
    return value


def catalog(search="", year=0, offset=0, liquid=-1):
    plan = _plan(str(_root()))
    rows = [r for r in _catalog()["streams"] if search.upper() in r["ticker"].upper()
            and (not year or r["year"] == year) and (liquid == -1 or r["liquid"] == bool(liquid))]
    progress = json.loads((_root() / "progress.json").read_text())
    return dict(streams=rows[offset:offset+100],total=len(rows),years=list(range(2019,2027)),
                frames=list(FRAMES),plan_hash=plan["hash"],view_version=VERSION,
                progress={k:progress.get(k) for k in ("state","bytes","units","updated")})


def _stream(key):
    if not re.fullmatch(r"[a-f0-9]{24}-20(?:19|2[0-6])",key):
        raise ValueError("Invalid frozen ticker/year ID")
    entry = next((r for r in _catalog()["streams"] if r["stream"] == key),None)
    if entry is None:
        raise ValueError("No sealed blocks in that ticker/year")
    plan = _plan(str(_root()))
    job = next((j for j in plan["jobs"] if j["ticker"] == entry["ticker"]),None)
    if job is None or digest(job["ticker"])[:24] != key[:24]:
        raise ValueError("Research index has foreign ticker identity")
    return plan,entry


def _receipts(key):
    plan,entry = _stream(key)
    rows=[]
    for path in sorted((_root()/"shards"/key[:24]).glob(f"{entry['year']}-*/receipt.json")):
        receipt=read_receipt(path.parent,plan["hash"])
        if receipt["ticker"] != entry["ticker"] or receipt["session_date"] != path.parent.name:
            raise ValueError("Foreign session receipt")
        if receipt["blocks"]:
            rows.append((path.parent,receipt))
    return rows


def units(stream):
    _,entry = _stream(stream)
    return dict(ticker=entry["ticker"],units=[dict(unit=int(r["session_date"].replace("-","")),
        blocks=r["blocks"],begin=r["session_date"],end=r["session_date"],storage=VERSION)
        for _,r in _receipts(stream)])


def sample(stream="",unit=-1,block=0,decision=4095,frame=0,view=-1,
           randomize=False,search="",year=0,liquid=-1):
    if frame not in range(5) or decision not in range(BLOCK) or block<0:
        raise ValueError("Invalid frame, origin or block")
    if not stream:
        choices=catalog(search,year,0,liquid)["streams"]
        if not choices:
            raise ValueError("No matching sealed session blocks yet")
        stream=random.choice(choices)["stream"]
        randomize=True
    plan,entry=_stream(stream)
    choices=[(p,r) for p,r in _receipts(stream) if unit<0 or int(r["session_date"].replace("-",""))==unit]
    if not choices:
        raise ValueError("No sealed session blocks in selected unit")
    directory,receipt=random.choice(choices) if randomize else choices[0]
    if randomize:
        block=random.randrange(receipt["blocks"])
    if block>=receipt["blocks"]:
        raise ValueError("Block outside selected session")
    reader=ShardReader(directory,plan["hash"])
    try:
        cached=reader.block(block)
        actions=[r for r in plan["authority"]["corporate_actions"]["rows"] if r["provider_ticker"]==entry["ticker"]]
        summaries=[]
        for f,h in enumerate(reader.geometry.history):
            count=(h-64)//64+1
            summaries.append(dict(frame=f,timeframe=FRAMES[f],context_bars=h,
                views=[dict(view=i,available=True,last_index=int(cached.latest[decision,f]-(count-1-i)*64)) for i in range(count)]))
        count=len(summaries[frame]["views"])
        chosen=count-1 if view<0 else view
        if not 0<=chosen<count:
            raise ValueError("Tokenizer view exceeds cached history")
        record=cached.windows(np.array([decision]),frame,actions=actions,stride=64)[0,chosen]
        import torch
        from ..transforms_v1 import BarInputTransformV1,TIME_CHANNELS

        transformed=BarInputTransformV1()(torch.from_numpy(record["values"].copy()).unsqueeze(0),
            torch.from_numpy(record["valid"].copy()).unsqueeze(0),torch.from_numpy(record["end_us"].copy()).unsqueeze(0),FRAMES[frame])
        bars=[]
        for i in range(64):
            valid=record["valid"][i].astype(bool)
            bars.append(dict(index=i,end_us=int(record["end_us"][i]),padding=False,
                gap_us=int(record["end_us"][i]-record["end_us"][i-1]) if i else None,
                raw=record["values"][i].tolist(),valid=valid.tolist(),
                values=[float(v) if ok else None for v,ok in zip(record["values"][i],valid)],
                time_values=transformed.values[0,i,18:].tolist()))
        revision=json.loads((_root()/"provenance.json").read_text())["source_revision"]
        return dict(stream=stream,ticker=entry["ticker"],year=entry["year"],liquid=entry["liquid"],
            unit=int(receipt["session_date"].replace("-","")),block=block,block_count=receipt["blocks"],
            decision=decision,decisions=cached.ends.tolist(),origin_us=int(cached.ends[decision]),
            frame=frame,view=chosen,summaries=summaries,channels=list(CHANNELS),time_channels=list(TIME_CHANNELS),
            bars=bars,split=cached.split,actions=[dict(r,effective_by_origin=bool(clock(r["execution_date"],4)<=cached.ends[decision])) for r in actions],
            receipt_hash=receipt["hash"],plan_hash=plan["hash"],producer_revision=revision,view_version=VERSION,
            price_basis=receipt["price_basis"],artifact_role="raw-ar-context-shards",training_ready=False)
    finally:
        for a in reader.arrays.values():
            if getattr(a,"_mmap",None) is not None:
                a._mmap.close()

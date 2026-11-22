"""Low-cost CPU health/trend receipt for the scheduled V21 reviewer."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
import time
from pathlib import Path
import psutil
from ..v5.shards.phase1_contract import write_json


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def inspect(root):
    state=read(root/'progress.json');owner=read(root/'process.json')
    alive=False;command=[]
    try:
        process=psutil.Process(owner['pid'])
        command=process.cmdline()
        alive=abs(process.create_time()-owner['created'])<.01 and command==owner['command']
    except (KeyError,psutil.Error):pass
    latest=[]
    for path in sorted(root.glob('validation-[0-9][0-9][0-9].json'))[-3:]:
        item=read(path);m=item['metrics']
        latest.append(dict(epoch=item['epoch'],step=item['step'],windows=item['windows'],
            close_mae_1s=m['all_visible/raw_mae/1s/trade_close'],
            masked_close_mae_1s=m['masked/raw_mae/1s/trade_close'],
            candle_direction_1s=m['all_visible/candle/1s/trade/moving_direction_pct'],
            volume_direction_1s=m['activity/all_visible/1s/trade_volume/moving_direction_pct'],
            daily_bid_range=m['all_visible/range/daily/bid/bin_accuracy_pct']))
    return dict(checked=time.time(),owner_alive=alive,pid=owner.get('pid'),creation_time=owner.get('created'),
                state=state,completed_full_validations=latest,stop_present=(root/'STOP').exists(),
                failures=[p.name for p in sorted(root.glob('failure-*.json'))],
                checkpoint=read(root/'checkpoint_last.proof.json'))


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=inspect(a.output);write_json(a.output/'monitor-latest.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()

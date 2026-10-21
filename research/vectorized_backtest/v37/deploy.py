"""Create a laptop runtime snapshot with exact certified dependency bytes."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--certified-history-producer',type=Path,required=True)
    p.add_argument('--certified-feature-cache-source',type=Path,required=True,help='Original certified v7 directory; preserve raw producer bytes')
    args=p.parse_args()
    source=Path(__file__).resolve().parents[3];output=args.output.resolve()
    runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v37').resolve()
    if not output.is_relative_to(runtime) or output.exists():raise ValueError('New V37 runtime snapshot directory required')
    raw=args.certified_history_producer.read_bytes();expected='e68c5464f3b20f2ccfae8352b9cfd87083e7a4136db8a3e03310811c3211f77f'
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Original certified history producer bytes required')
    current=(source/'research/vectorized_backtest/v6/torch_backtest/history_bank.py').read_bytes()
    if current.replace(b'\r\n',b'\n')!=raw.replace(b'\r\n',b'\n'):raise ValueError('History producer source differs beyond certified line endings')
    certified={'research/vectorized_backtest/v6/torch_backtest/history_bank.py':raw}
    for name,checksum in {'feature_cache.py':'103f842e7c8028ca2b02acc577b4798baff08d29ed89dc19100a7c1e0c105a47',
                          'data.py':'2c9fd0a6047b8748c1f58cb9cc558e6682e999664020a10fa7050f7c1916a567'}.items():
        rel='research/vectorized_backtest/v7/'+name;original=(args.certified_feature_cache_source/name).read_bytes()
        if hashlib.sha256(original).hexdigest()!=checksum:raise ValueError('Original certified feature-cache bytes required: '+name)
        if (source/rel).read_bytes().replace(b'\r\n',b'\n')!=original.replace(b'\r\n',b'\n'):
            raise ValueError('Feature-cache producer differs beyond certified line endings: '+name)
        certified[rel]=original
    # Git supplies a bounded source inventory; no runtime outputs, dependencies,
    # environment files, secrets, notebooks or unrelated untracked files copied.
    paths=subprocess.check_output(['git','ls-files','--','research','pipelines','src','services','arte'],cwd=source,text=True).splitlines()
    records={}
    for rel in paths:
        f=source/rel
        if f.suffix!='.py':continue
        b=f.read_bytes()
        if rel in certified:b=certified[rel]
        dest=output/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b);records[rel]=hashlib.sha256(b).hexdigest()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    (output/'deployment.json').write_text(json.dumps(dict(commit=commit,source=str(source),files=records,history_producer_sha256=expected),indent=2))
    print(output)
if __name__=='__main__':main()

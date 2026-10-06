"""Prepare a V4 broker snapshot from the feature bank's certified market build."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from datetime import datetime
from .encoding.config import Session
from .encoding.clickhouse import certify_source
from .prepare import prepare_tape
from .prepared_cache import save_prepared,load_prepared
from .genome import StrategySpace
from .runtime import DEFAULT,code_hash,file_hash,require_runtime

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--date',required=True)
    p.add_argument('--maximum-tape-gib',type=float,default=12.)
    args=p.parse_args(argv);spec=json.loads(args.sessions.read_text())
    rows=[s for s in spec['training'] if s['day']==args.date]
    if len(rows)!=1:raise ValueError('Prepare training only; final validation remains sealed')
    item=rows[0];root=Path(item['execution_root'])
    require_runtime(root.parent)
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from research.mlops.env import load_env_files
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    from .availability import configure_reader
    configure_reader(Path(__file__).resolve().parents[4])
    # Deep campaign paths plus SHA256 directories and atomic-write suffixes
    # exceed Windows MAX_PATH. Session/source keys seal this shared private cache.
    session=Session(manifest=Path(item['source_manifest']),ledger=Path(item['source_ledger']),runtime=DEFAULT/'preparation',
        start=datetime.fromisoformat(item['start']),end=datetime.fromisoformat(item['end']))
    certificate=certify_source(session)
    plan=json.loads((Path(item['feature_root'])/'plan.json').read_text())
    if certificate.source['build_id']!=plan['source_build_id']:raise ValueError('Broker/feature build mismatch before preparation')
    identity=dict(code_hash=code_hash(),session=item,manifest_sha256=file_hash(session.manifest),build_id=certificate.source['build_id'])
    value=load_prepared(root,identity)
    if value is None:
        value=prepare_tape(session,StrategySpace().settings,maximum_gib=args.maximum_tape_gib,
            progress=lambda e:print(json.dumps(e),flush=True))
        save_prepared(root,value,identity)
    print(json.dumps(dict(status='complete',day=item['day'],tickers=len(value.tickers),bytes=value.bytes,source=value.provenance['source_build'])))
    return 0

if __name__=='__main__':raise SystemExit(main())

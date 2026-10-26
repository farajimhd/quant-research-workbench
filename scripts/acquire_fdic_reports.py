"""Acquire only the four reviewed banks; shared inventory for backfill/forward fill."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json
from concurrent.futures import ThreadPoolExecutor
import requests
from pipelines.fundamentals.fdic_reports import BASE,CERTIFICATES,inventory,acquire,retained_root
from scripts.run_fundamentals import save,lock
from pipelines.certified_research.contracts import digest


def fetch(session,cert):
    with session.get(BASE+'/cert/'+str(cert),stream=True,timeout=(10,60),allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code!=200:raise ValueError('Unexpected FDIC inventory response')
        parts=[];size=0
        for block in response.iter_content(128*1024):
            size+=len(block)
            if size>8*1024**2:raise ValueError('FDIC inventory exceeds metadata bound')
            parts.append(block)
    return json.loads(b''.join(parts))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',required=True);parser.add_argument('--inventory-file',required=True)
    parser.add_argument('--start',required=True);parser.add_argument('--end',required=True)
    parser.add_argument('--allow-acquisition',action='store_true')
    parser.add_argument('--changed-since',help='Aware revision watermark; include changed older attachments')
    parser.add_argument('--allow-revisions',action='store_true',help='Retain changed attachment metadata in a separate version directory')
    args=parser.parse_args()
    if not args.allow_acquisition:parser.error('Official acquisition requires --allow-acquisition')
    root=Path(args.source_root).resolve();target=Path(args.inventory_file).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not target.is_relative_to(runtime):raise ValueError('Use available operational runtime root')
    root.mkdir(parents=True,exist_ok=True);target.parent.mkdir(parents=True,exist_ok=True)
    def bank(pair):
        ticker,cert=pair
        with requests.Session() as session:
            reports=inventory(fetch(session,cert),ticker,args.start,args.end,changed_since=args.changed_since)
            receipts=[acquire(report,retained_root(root/'verified',report) if args.allow_revisions else root/'verified',session) for report in reports]
            if inventory(fetch(session,cert),ticker,args.start,args.end,changed_since=args.changed_since)!=reports:raise ValueError('FDIC metadata changed during acquisition; preserve receipts and review')
        return reports,dict(ticker=ticker,reports=len(reports),downloaded=sum(not r['reused'] for r in receipts),reused=sum(r['reused'] for r in receipts))
    with lock(root/'acquisition.lock'),ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(bank,CERTIFICATES.items()))
        reports=[r for rows,_ in results for r in rows]
        if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=reports:raise ValueError('Use a new inventory file for a new forward interval; preserve old requests')
        save(target,reports);save(target.with_suffix('.acquisition.json'),dict(inventory_hash=digest(reports),banks=[s for _,s in results]))
        for _,summary in results:print(json.dumps(summary),flush=True)


if __name__=='__main__':main()

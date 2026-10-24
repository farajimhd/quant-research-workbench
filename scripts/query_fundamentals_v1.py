"""Bounded selected fundamentals as-of/history and gap inspection."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json
from datetime import datetime
from scripts.run_fundamentals import Client
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files
from pipelines.fundamentals.v1 import financial_sql,reference_sql,coverage_sql,gap_sql


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('financial','reference','coverage','gaps'))
    p.add_argument('--entities',nargs='+',required=True,help='Reviewed CIKs for financials, security IDs for references')
    p.add_argument('--metrics',nargs='+')
    p.add_argument('--as-of',required=True,type=datetime.fromisoformat)
    p.add_argument('--since',type=datetime.fromisoformat)
    p.add_argument('--known-at',type=datetime.fromisoformat)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    a=p.parse_args()
    if a.mode=='financial':sql=financial_sql(a.entities,a.as_of,since=a.since,known_at=a.known_at)
    elif a.mode=='reference':
        if not a.metrics:p.error('Reference queries require --metrics')
        sql=reference_sql(a.entities,a.metrics,a.as_of,since=a.since)
    else:
        if a.since or a.known_at:p.error('Coverage/gaps use --as-of only')
        sql=(coverage_sql if a.mode=='coverage' else gap_sql)(a.entities,a.as_of)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    client=Client(a.endpoint)
    try:
        for row in client.query(sql):print(json.dumps(row,ensure_ascii=False))
    finally:client.close()


if __name__=='__main__':main()

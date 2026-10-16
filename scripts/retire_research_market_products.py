"""Retire the exact user-approved duplicate products; preserve shared/structural data."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from contextlib import closing
import json
from datetime import datetime,timezone
from scripts.build_market_day import Client,save
from pipelines.certified_research.retired_market import TABLES,PRODUCTS
from pipelines.certified_research.contracts import literal
from pipelines.certified_research.publication import DictionaryClaim
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def retire(query,root,claim):
    selected=','.join(literal(t) for t in TABLES)
    # The caller has stopped old campaigns. Reject active consumers/writers and
    # SQL-level dependencies rather than dropping underneath them.
    busy=query(f"SELECT query_id FROM system.processes WHERE query NOT LIKE '%system.processes%' "
        f"AND arrayExists(t->position(query,t)>0,[{selected}])")
    dependencies=query(f"SELECT database,name FROM system.tables WHERE engine IN ('View','MaterializedView','Dictionary') "
        f"AND arrayExists(t->position(create_table_query,t)>0,[{selected}])")
    if busy or dependencies:raise ValueError('Research product consumers still active: '+str(busy+dependencies))
    path=root/'retirement.json'
    if path.exists():receipt=json.loads(path.read_text())
    else:
        receipt=dict(version=1,requested_tables=list(TABLES),started_at=datetime.now(timezone.utc).isoformat(),
            definitions=query(f"SELECT name,create_table_query FROM system.tables WHERE database='arte' AND name IN ({selected})"),
            parts=query(f"SELECT table,sum(rows) rows,sum(bytes_on_disk) bytes FROM system.parts WHERE database='arte' AND active AND table IN ({selected}) GROUP BY table"),dropped=[])
    if receipt['requested_tables']!=list(TABLES):raise ValueError('Retirement receipt scope differs')
    save(path,receipt)
    for index,table in enumerate(TABLES,1):
        claim.check()
        query(f'DROP TABLE IF EXISTS arte.{table} SYNC',read=False)
        if table not in receipt['dropped']:receipt['dropped'].append(table)
        save(path,receipt);print(f'[{index}/{len(TABLES)}] Removed arte.{table}',flush=True)
    claim.check()
    # Retain unrelated reference and structural receipts; remove only receipts
    # that would otherwise claim publication for these deleted products.
    query("ALTER TABLE arte.research_unit_receipt_v1 DELETE WHERE product IN ("+
          ','.join(literal(p) for p in PRODUCTS)+") SETTINGS mutations_sync=2",read=False)
    remaining=query(f"SELECT name FROM system.tables WHERE database='arte' AND name IN ({selected})")
    if remaining:raise ValueError('Retired tables still present: '+str(remaining))
    stale=query("SELECT count() n FROM arte.research_unit_receipt_v1 WHERE product IN ("+
                ','.join(literal(p) for p in PRODUCTS)+")")[0]
    if int(stale['n']):raise ValueError('Retired product receipts still present')
    receipt.update(status='complete',completed_at=datetime.now(timezone.utc).isoformat())
    save(path,receipt)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--env-file',type=Path,default=Path('D:/TradingML/secrets/.env'))
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    p.add_argument('--execute',action='store_true')
    a=p.parse_args(argv)
    if not a.execute:
        print('Retirement scope:\n'+'\n'.join('arte.'+t for t in TABLES));return 0
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':raise ValueError('Execute on workstation')
    root=Path('D:/TradingML/runtimes').resolve()
    if not root.is_dir() or not a.run_dir.resolve().is_relative_to(root):raise ValueError('Runtime root unavailable or invalid')
    a.run_dir.mkdir(parents=True,exist_ok=True)
    load_env_files([a.env_file],verbose=False);os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    a.max_threads=2;a.max_memory_gb=2;a.query_timeout=600
    with closing(Client(a)) as c,closing(open_workstation_keeper_session()) as keeper:
        claim=DictionaryClaim(keeper.client,'arte')
        try:retire(c.query,a.run_dir,claim)
        finally:claim.close()
    print('Retirement verified; shared/reference and structural tables retained.');return 0


if __name__=='__main__':raise SystemExit(main())

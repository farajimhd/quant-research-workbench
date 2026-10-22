"""Export a frozen retained population; no live account eligibility claim."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import date
from pipelines.fundamentals.population import snapshot_population
from scripts.run_fundamentals import Client,save
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot-date',required=True,type=date.fromisoformat)
    p.add_argument('--output',required=True)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    a=p.parse_args();root=Path('D:/TradingML/runtimes').resolve();out=Path(a.output).resolve()
    if not root.is_dir() or not out.is_relative_to(root) or out.exists():
        raise ValueError('Output must be a NEW file under available D:/TradingML/runtimes')
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c=Client(a.endpoint)
    try:
        result=snapshot_population(c.query,a.snapshot_date)
        out.parent.mkdir(parents=True,exist_ok=True);save(out,result)
        x=result['provenance']
        print(f"Frozen snapshot {a.snapshot_date}: {x['population_rows']:,} symbols; "
            f"{x['sec_unresolved_symbols']:,} unresolved SEC mappings.")
        print('Snapshot cohort only; not proof of historical membership or current buy eligibility.')
    finally:c.close()


if __name__=='__main__':main()

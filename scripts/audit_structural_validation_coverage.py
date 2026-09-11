"""Read-only clock-authority preflight for the blocked structural holdout."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime,timezone
from hashlib import sha256

from scripts.repair_qmd_live_canonical_bars import load_dotenv,connection_from_env
from scripts.audit_structural_baseline import write
from scripts.swing_book_paths import validate_runtime_root


def main(root):
    root=validate_runtime_root(root);root.mkdir(parents=True,exist_ok=True)
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    client=connection_from_env('q_live')
    queries=dict(
        coverage_population="SELECT source_date,uniqExact(ticker) AS tickers FROM historical_event_execution_clock_coverage_v1 GROUP BY source_date ORDER BY source_date FORMAT JSONEachRow",
        missing_sidecar_rows="SELECT source_date,ticker,count() AS rows FROM historical_event_execution_clock_v1 WHERE ticker IN ('SUGP','JUNS') AND source_date IN ('2026-08-13','2026-08-24','2026-08-25') GROUP BY source_date,ticker SETTINGS max_execution_time=20,max_threads=2 FORMAT JSONEachRow",
        live_clock_rows="SELECT event_date,ticker,count() AS rows,countIf(execution_timestamp_us>0) AS clocks FROM q_live.events WHERE ticker IN ('SUGP','JUNS') AND event_date IN ('2026-08-13','2026-08-24','2026-08-25') GROUP BY event_date,ticker SETTINGS max_execution_time=20,max_threads=2 FORMAT JSONEachRow")
    result=dict(as_of=datetime.now(timezone.utc).isoformat(),source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),read_only=True)
    for name,sql in queries.items():
        result[name]=client.json_rows(sql,timeout=30)
        print(f'{name}: {len(result[name])} result rows',flush=True)
    write(root/'coverage-preflight.json',result)
    print(f'Completed read-only preflight: {root / "coverage-preflight.json"}')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    main(p.parse_args().runtime)

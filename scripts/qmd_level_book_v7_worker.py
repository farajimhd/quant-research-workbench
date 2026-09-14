"""Private JSON-lines worker owned by QMD; stdout is protocol only."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from src.market_engine.v7_qmd import Service


def main():
    from research.level_book.v7.campaign import load_env_files,discover_clickhouse_env_files
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    service=None
    for line in sys.stdin:
        try:
            if len(line)>65536:raise ValueError('V7 request exceeds protocol limit')
            request=json.loads(line)
            if service is None:service=Service()
            if request.get('operation')=='catalog':result=service.catalog.items()
            elif request.get('operation')=='coverage':
                result=service.coverage(request['tickers'],request['as_of'])
            elif request.get('operation')=='chart_checkpoint':
                result=service.chart_checkpoint(request['ticker'],request['as_of'],request['mode'])
            elif request.get('operation')=='snapshot':
                if request.get('delta',False):
                    if request.get('include_segments'):raise ValueError('V7 delta excludes chart segments')
                    result=service.snapshot_delta(request['ticker'],request['as_of'],request['mode'],request.get('cursor_id',''),request.get('base_version'))
                else:
                    result=service.snapshot(request['ticker'],request['as_of'],request['mode'],request.get('include_segments',False),request.get('cursor_id',''))
            else:raise ValueError('Unsupported V7 operation')
            response=dict(ok=True,result=result)
        except Exception as exc:
            response=dict(ok=False,error=str(exc))
        print(json.dumps(response,separators=(',',':'),allow_nan=False),flush=True)


if __name__=='__main__':main()

"""Direct bounded V6 label replacement launcher."""
import os
from pathlib import Path
import sys

os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

if __name__=='__main__':
    from research.rl_trading.v6.opportunity_dataset import main
    print('V6 swing labels: price units, no legacy candidate/teacher input; bounded workers.',flush=True)
    main()

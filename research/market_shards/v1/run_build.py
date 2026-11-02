"""Direct Python launcher; source trees never receive bytecode or artifacts."""
import os
import sys
from pathlib import Path
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
if __name__=='__main__':
    print('Running: '+sys.executable+' -B -u -m research.market_shards.v1.build '+' '.join(sys.argv[1:]),flush=True)
    from research.market_shards.v1.build import main
    main()

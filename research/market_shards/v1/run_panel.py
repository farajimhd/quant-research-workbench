"""Direct progress observer launcher."""
import os
import sys
from pathlib import Path
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
if __name__=='__main__':
    from research.market_shards.v1.progress import main
    main()

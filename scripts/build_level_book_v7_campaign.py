"""Run or monitor the all-tradable historical V7 MLE campaign."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[name]='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research.level_book.v7.campaign import main
if __name__=='__main__':main()

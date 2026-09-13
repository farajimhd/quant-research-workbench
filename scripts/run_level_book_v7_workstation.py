"""Resume the pinned V7 campaign with a bounded workstation process pool."""
import os
import sys
from pathlib import Path

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.level_book.v7.workstation import main

if __name__ == '__main__':
    main()

"""Single-stage V24 launcher; no local pretraining or predecessor qualification."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from .run_search import main

if __name__=='__main__':raise SystemExit(main())

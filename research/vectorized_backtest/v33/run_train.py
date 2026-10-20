"""V33 global-only entry point."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from .run_global_train import main
if __name__=='__main__':
    import sys
    sys.exit(main())

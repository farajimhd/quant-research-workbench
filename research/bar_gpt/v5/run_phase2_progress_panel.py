"""Open the read-only Rich phase-two worker console."""
import os
from pathlib import Path
import sys

os.environ['PYTHONDONTWRITEBYTECODE']='1'
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from research.bar_gpt.v5.phase2_progress_panel import main

if __name__=='__main__':raise SystemExit(main())

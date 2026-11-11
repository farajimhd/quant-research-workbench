"""Direct native entry point for the continuing phase-two shard campaign."""
import os
import sys
from pathlib import Path
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from research.bar_gpt.v5.phase2_build_campaign_v1 import main
if __name__=='__main__':raise SystemExit(main())

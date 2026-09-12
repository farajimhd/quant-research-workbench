"""Audit a completed level-model run without fitting or changing predictions."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from research.reaction_levels.v1.audit import run

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,default=Path(r'D:\TradingML\runtimes\reaction-level-model\AAPL-jul-aug2026-v1-batched'))
    args=p.parse_args();root=args.runtime.resolve();allowed=Path(r'D:\TradingML\runtimes').resolve()
    if not allowed.is_dir() or not root.is_relative_to(allowed):raise ValueError('Invalid runtime root')
    result=run(root)
    print(f"Audit complete. Recent-frequency baseline log loss: {result['calibration_frequency_baseline']['log_loss']:.4f}; report.md",flush=True)

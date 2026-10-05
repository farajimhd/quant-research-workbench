"""Prepare all certified phase-1b shards without starting teacher/PPO training."""
import os
from pathlib import Path
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','4')
os.environ.setdefault('OMP_NUM_THREADS','1')
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
if __name__=='__main__':
    from research.rl_trading.v6.market_teacher_dataset import main
    try:raise SystemExit(main())
    except Exception as error:
        from research.rl_trading.v6.opportunity_dataset import write_json
        if '--output' in sys.argv:
            output=Path(sys.argv[sys.argv.index('--output')+1]).resolve()
            if output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
                write_json(output/'progress.json',dict(status='failed',reason=str(error)))
        raise

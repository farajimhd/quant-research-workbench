"""Direct Python launcher with safe defaults and explicit command display."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import sys


if __name__=='__main__':
    arguments=sys.argv[1:]
    if '--output' not in arguments:
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        arguments=['--output',f'D:/TradingML/runtimes/rl-trading/v7/fresh-{stamp}',*arguments]
    command=[sys.executable,'-B','-u','-m','research.rl_trading.v7.train',*arguments]
    print(subprocess.list2cmdline(command),flush=True)
    raise SystemExit(subprocess.call(command,cwd=Path(__file__).resolve().parents[3]))

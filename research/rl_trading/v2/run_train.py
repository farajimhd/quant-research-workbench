"""Direct Python launcher; no services, synchronization, or remote execution."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import subprocess

if __name__ == '__main__':
    command = [sys.executable,'-B','-u',str(Path(__file__).with_name('train.py')),*sys.argv[1:]]
    print(subprocess.list2cmdline(command),flush=True)
    raise SystemExit(subprocess.call(command,env=os.environ.copy()))

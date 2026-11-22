"""Logging entry point for an independently launched workstation process."""
import os,sys,argparse
from pathlib import Path
os.environ.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')


def main():
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--console-log',type=Path,required=True);a,rest=p.parse_known_args()
    path=a.console_log.resolve();root=Path('D:/TradingML/runtimes').resolve()
    if not path.is_relative_to(root) or not path.parent.is_dir():raise ValueError('Existing workstation runtime log directory required')
    with path.open('a',encoding='utf-8',buffering=1) as stream:
        os.dup2(stream.fileno(),1);os.dup2(stream.fileno(),2)
        sys.stdout.reconfigure(encoding='utf-8',line_buffering=True);sys.stderr.reconfigure(encoding='utf-8',line_buffering=True)
        sys.argv=[sys.argv[0],*rest]
        from .run_full import main as run
        run()


if __name__=='__main__':main()

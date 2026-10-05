"""Python launcher for the audited V6 training CLI."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')

if __name__=='__main__':
    import sys
    import shlex
    if not __package__:
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    print(shlex.join([sys.executable,'-m','research.rl_trading.v6.train',*sys.argv[1:]]),flush=True)
    from research.rl_trading.v6.train import main
    raise SystemExit(main())

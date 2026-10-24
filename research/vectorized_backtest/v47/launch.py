"""Launch the immutable V47 laptop experiment outside the desktop app process tree."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
import json
from pathlib import Path
import sys
import subprocess
from .scheduled_launch import install
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--initial-pool', type=Path, required=True)
    parser.add_argument('--task', default='Codex-V47-Aug13-50')
    parser.add_argument('--authorize-run', action='store_true', required=True)
    args = parser.parse_args()
    code = Path(__file__).resolve().parents[3]
    require_runtime(code)
    output = require_runtime(args.output)
    if (output / 'active.json').exists() or (output / 'entry.py').exists():
        raise ValueError('Existing experiment: inspect ownership/checkpoint before resuming')
    from research.vectorized_backtest.v42.run_search import source_hash as base_hash
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    from .identity import source_hash
    job=json.loads(args.job.read_bytes())
    if base_hash()!=job['source_sha256'] or file_hash(job['training'])!=job['training_sha256']:
        raise ValueError('Original optimizer/training identity changed before scheduler launch')
    deployment=json.loads((code/'deployment.json').read_bytes())
    (output / 'manifest.json').write_text(json.dumps(dict(version='v47',
        days=['2026-08-13'], local_generations=50, population=1024,
        seed=2020, initial_candidates=6144, validation_opened=False,
        source_commit=deployment['commit'],source_sha256=source_hash(),
        code=str(code),output=str(output),job=str(args.job),job_sha256=file_hash(args.job),
        initial_pool=str(args.initial_pool),initial_pool_sha256=file_hash(args.initial_pool),
        quote_load_mode='resident-unconditional',wandb=None)), encoding='utf-8')
    entry = output / 'entry.py'
    entry.write_text("import os,sys\nos.environ['PYTHONDONTWRITEBYTECODE']='1'\n"
        "os.environ.update(V42_CPU_RESERVE_GIB='12',V42_CPU_ADMISSION_GIB='8',V42_PREPARATION_WORKERS='2',V47_MASK_QUOTES='0')\n"
        f"code={str(code)!r}\nos.chdir(code);sys.path.insert(0,code)\n"
        "from research.vectorized_backtest.v47.run_test import main\n"
        "if __name__=='__main__':main()\n", encoding='utf-8')
    launcher = output / 'scheduled_launch.py'
    from . import scheduled_launch
    launcher.write_bytes(Path(scheduled_launch.__file__).read_bytes())
    arguments = ['--job', str(args.job), '--output', str(output), '--day', '2026-08-13',
                 '--initial-pool', str(args.initial_pool), '--authorize-run']
    print('V47: 50 generations, population 1024, exact original 6144 candidates', flush=True)
    print(subprocess.list2cmdline([sys.executable,'-B','-m','research.vectorized_backtest.v47.run_test',*arguments]),flush=True)
    print(install(args.task, Path(sys.executable).with_name('pythonw.exe'), entry, code,
                  arguments, launcher), flush=True)


if __name__ == '__main__':
    main()

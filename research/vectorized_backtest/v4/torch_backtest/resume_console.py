"""Resume an immutable worker with one separate, read-only console renderer.

Display-only changes must not change a strategy population, RNG, source seal or
partial-generation receipts. The certified old worker runs with --plain into a
runtime log; this fresh UI is the sole owner of the visible terminal.
"""

import os
import sys

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import argparse
import json
import subprocess
from time import sleep
import traceback

from research.vectorized_backtest.v4.torch_backtest.optimization_ui import SearchPanel
from research.vectorized_backtest.v4.torch_backtest.runtime import DEFAULT, file_hash, require_runtime, write_json


def resume_arguments(job):
    """Preserve original CLI exactly, changing only output to exact resume/plain."""
    args = json.loads((job / 'command.json').read_text())
    for flag in ('--output', '--resume'):
        if flag in args:
            index = args.index(flag)
            del args[index:index + 2]
    args += ['--resume', str(job / 'experiment')]
    if '--plain' not in args:
        args.append('--plain')
    return args


def verify_worker(checkout, job):
    """Fail closed on changed source, checkpoint absence or frozen completion."""
    identity = json.loads((job / 'experiment' / 'identity.json').read_text())
    marker = DEFAULT / 'deployments' / checkout.name / 'deployment.json'
    deployment = json.loads(marker.read_text())
    if deployment['v4_code_hash'] != identity['code_hash']:
        raise ValueError('Worker deployment does not own the experiment source seal')
    for name, expected in deployment['files'].items():
        if file_hash(checkout / name) != expected:
            raise ValueError('Immutable worker source changed: ' + name)
    if not (job / 'experiment' / 'checkpoint.json').is_file():
        raise ValueError('Exact resume requires an existing checkpoint')
    if (job / 'experiment' / 'report.json').exists():
        raise ValueError('Completed evaluation cannot restart')
    return identity


def read_snapshot(path):
    """A transient observer read failure must not kill the training worker."""
    try:
        return json.loads(path.read_text(encoding='utf-8')), None
    except (OSError, json.JSONDecodeError) as error:
        return None, f'{type(error).__name__}: {error}'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker-checkout', type=Path, required=True)
    parser.add_argument('--worker-job', type=Path, required=True)
    args = parser.parse_args(argv)
    output = require_runtime(args.output)
    if (output / 'worker.json').exists():
        raise ValueError('Console job already owns a worker; use a new console job')
    identity = verify_worker(args.worker_checkout, args.worker_job)
    command = [sys.executable, '-B', '-m',
               'research.vectorized_backtest.v4.torch_backtest.optimize',
               *resume_arguments(args.worker_job)]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', NO_COLOR='1')
    with (output / 'worker.log').open('x', encoding='utf-8') as log:
        worker = subprocess.Popen(command, cwd=args.worker_checkout, env=env,
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        write_json(output / 'worker.json', dict(
            pid=worker.pid, parent_pid=os.getpid(), command=command,
            checkout=str(args.worker_checkout), experiment=str(args.worker_job / 'experiment'),
            worker_code_hash=identity['code_hash'], rendering='one_read_only_panel; worker_plain_log'))
        try:
            with SearchPanel(args.worker_job / 'experiment', read_only=True) as panel:
                while True:
                    snapshot = args.worker_job / 'experiment' / 'status.json'
                    state, read_error = read_snapshot(snapshot)
                    if state is not None:
                        panel.state = state
                        if panel.live:
                            panel.live.update(panel.view())
                        elif panel.state != getattr(panel, 'previous', None):
                            panel.console.print(panel.view())
                        panel.previous = dict(panel.state)
                    elif read_error != getattr(panel, 'last_read_error', None):
                        write_json(output / 'observer_read.json', dict(
                            status='retrying', reason=read_error,
                            worker_pid=worker.pid, last_good_status=panel.state.get('status')))
                    panel.last_read_error = read_error
                    code = worker.poll()
                    if code is not None:
                        if code:
                            panel.console.print(f'Worker failed (exit {code}); see {output / "worker.log"}')
                        return code
                    sleep(1)
        except BaseException as error:
            # Store the actual supervisor traceback before cleanup. The worker
            # log alone cannot explain an exception in this separate process.
            write_json(output / 'supervisor_error.json', dict(
                type=type(error).__name__, reason=str(error),
                traceback=traceback.format_exc(), worker_pid=worker.pid))
            raise
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
            write_json(output / 'worker_exit.json', dict(
                exit_code=worker.returncode, worker_pid=worker.pid))


if __name__ == '__main__':
    raise SystemExit(main())

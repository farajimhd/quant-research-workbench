"""One online project for all phase-two versions, preserving same-run recovery."""
import os
import importlib.util
from ..v5.reporting_v1 import Reporter

PROJECT = 'bargpt-phase2'


def require_online_credentials():
    if not os.environ.get('WANDB_API_KEY'):
        raise RuntimeError('WANDB_API_KEY is required for phase-two online training')
    if importlib.util.find_spec('wandb') is None:
        raise RuntimeError('wandb must be installed for phase-two online training')


def online_reporter(output, manifest):
    reporter = Reporter(output, manifest, mode='online', project=PROJECT)
    if reporter.run is None:
        reporter.finish('failed','Phase-two training requires online W&B')
        raise RuntimeError('Phase-two training requires online W&B; no silent local-only fallback')
    return reporter

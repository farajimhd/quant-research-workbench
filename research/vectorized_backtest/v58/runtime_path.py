"""Bounded recovery for transient runtime-path probes; never change authority."""
import logging
from pathlib import Path
from time import sleep
from research.vectorized_backtest.v6.torch_backtest import runtime


def _diagnostic(path):
    result={'requested':str(path),'root':str(runtime.ROOT)}
    for name,value in (('root',runtime.ROOT),('target',Path(path))):
        try:
            resolved=value.resolve()
            result[name+'_resolved']=str(resolved)
            result[name+'_is_dir']=resolved.is_dir()
            # Unlike is_dir, stat retains the underlying Windows error.
            result[name+'_stat_mode']=resolved.stat().st_mode
        except OSError as error:
            result[name+'_error']=repr(error)
    return result


def require_runtime(path):
    """Retry the complete original guard, never mkdir after a failed check."""
    for attempt in range(3):
        try:
            return runtime.require_runtime(path)
        except (OSError,ValueError) as error:
            details=_diagnostic(path)
            logging.getLogger(__name__).warning(
                'Runtime path check attempt %s/3 failed: %r; %s',attempt+1,error,details)
            if attempt==2:
                raise RuntimeError(f'Runtime path unavailable or outside authority: {details}') from error
            sleep((.1,.5)[attempt])

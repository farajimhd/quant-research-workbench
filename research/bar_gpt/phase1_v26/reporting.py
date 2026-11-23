"""Checkpointable W&B transport failures; local evidence always comes first."""
import json
from ..v5.reporting_v1 import Reporter as BaseReporter


class ReportingFailure(RuntimeError):
    pass


def transport_failure(error):
    # Retry only a broken local transport, not configuration/auth/program errors.
    return isinstance(error,(ConnectionError,BrokenPipeError)) or (
        isinstance(error,OSError) and getattr(error,'winerror',None) in (64,109,232,10054)) or (
        type(error).__name__=='MailboxClosedError' and type(error).__module__.startswith('wandb.'))


class Reporter(BaseReporter):
    def log(self,metrics,step):
        reported=step+self.global_step_offset
        self.journal.write(json.dumps(dict(step=step,reported_global_step=reported,**metrics),allow_nan=False)+'\n')
        if self.run:
            try:self.run.log(dict(metrics,global_step=reported,optimizer_global_step=step))
            except Exception as error:
                if not transport_failure(error):raise
                raise ReportingFailure(f'W&B transport failed at optimizer step {step}: {error!r}') from error

    def close_local(self,state,error):
        """Do not call the broken service while preserving the original failure."""
        self.stop.set();self.thread.join(timeout=15)
        if not self.journal.closed:self.journal.close()
        self.update(state=state,error=error);self.publish()
        if self.writer_lease:self.writer_lease.close()

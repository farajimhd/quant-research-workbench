"""Unified SEC catch-up handoff; child completion is never certification."""
from datetime import date,timedelta
import sys
from . import acquisition,planning,sec_coverage,sec_lineage
from .contracts import literal


def command(paths,config,start,runtime_root_ch):
    if type(start) is not date or not date(2019,1,1)<=start<=config.start:
        raise ValueError('SEC acquisition needs a supported fundamental context bound')
    workers=min(config.workers,8,config.global_memory_bytes//config.query_memory_bytes)
    if workers<1:raise ValueError('SEC acquisition query memory exceeds the global budget')
    end=planning.reference_coverage_end(config.end)+timedelta(days=1)
    result=(sys.executable,'-B',str(acquisition.ROOT/'pipelines/sec/edgar/sec_historical_gap_fill.py'),
        '--execute','--runtime-root-win',str(paths.runtime/'sec'),'--start-date',str(start),'--end-date',str(end),
        '--context-start-date',str(start),'--read-database','q_live','--write-database','q_live',
        '--context-database','q_live',
        '--execution-workers',str(workers),'--execution-query-threads',str(config.query_threads),
        '--execution-query-memory-bytes',str(config.query_memory_bytes),
        '--execution-global-memory-bytes',str(config.global_memory_bytes),'--progress-layout','text')
    return result+(('--runtime-root-ch',runtime_root_ch) if runtime_root_ch is not None else ())


def prepare(query,paths,config,claim,stop,*,fundamental_start,acquire_missing=False,
            runtime_root_ch=None,runner=acquisition.run_ingestion,emit=print):
    # Validate dates/resources even on verified reuse; no hidden 2019 rebuild.
    invocation=command(paths,config,fundamental_start,runtime_root_ch)
    end=planning.reference_coverage_end(config.end)
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('SEC preparation interrupted')
    def inspect():
        guard()
        exists=query(f"SELECT count() n FROM system.tables WHERE database='q_live' AND name={literal(sec_coverage.TABLE)}")
        if len(exists)!=1:raise ValueError('SEC source metadata is ambiguous')
        if int(exists[0]['n']):sec_lineage.qualify(query,claim,fundamental_start,end,stop=stop)
        result=sec_coverage.inventory(query,fundamental_start,end)
        guard();return result
    coverage=inspect()
    if coverage['ready']:return dict(status='reused',coverage=coverage)
    if not acquire_missing:raise ValueError('SEC source coverage is incomplete; select --acquire-sec-missing to prepare it')
    if runtime_root_ch is None:raise ValueError('SEC catch-up requires --sec-runtime-root-ch with a verified runtime mount alias')
    emit(f'SEC catch-up | context={fundamental_start} | through={end} | missing={len(coverage["gaps"])} families')
    runner(invocation,paths,stop,emit=emit,guard=claim.check)
    coverage=inspect()
    if not coverage['ready']:raise ValueError('SEC catch-up did not certify every required acquisition family and stage closure')
    return dict(status='prepared',coverage=coverage)

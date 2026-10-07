"""One-owner serial/pipelined cold/warm full-session timing and fill parity."""
import argparse,json,os,time
from pathlib import Path
from . import profile_staged
from .profile_execution import compare
from .runtime import require_runtime,write_json,file_hash,code_hash


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--population',type=int,default=256);p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--session-count',type=int,default=1)
    args=p.parse_args(argv)
    if args.population<=args.batch_size:p.error('Full overlap comparison requires multiple candidate batches')
    root=require_runtime(args.output)
    if (root/'identity.json').exists():raise ValueError('Immutable profiling output already exists')
    write_json(root/'identity.json',dict(code_hash=code_hash(),sessions_sha256=file_hash(args.sessions),
        arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}))
    lock=root/'owner.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
    started=time.time();rows=[];audits=[]
    try:
        for phase,(mode,warm) in enumerate((('serial',False),('serial',True),('pipeline',False),('pipeline',True))):
            child=root/f'{mode}_{"warm" if warm else "cold"}'
            def emit(status):
                if status.get('status')=='profile_complete':status['status']='profiling'
                status.update(started_epoch=started,execution_pass=phase+1,execution_passes=4)
                status['stage']=f'{mode} / {"warm" if warm else "cold"} | '+status.get('stage','')
                write_json(root/'status.json',status)
                if (root/'STOP').exists():write_json(child/'STOP',dict(reason='Owned parent stop requested'))
            flags=['--rule-prefetch','--prefetch-features','--concurrent-writes'] if mode=='pipeline' else ['--no-rule-prefetch','--no-prefetch-features','--no-concurrent-writes']
            result=profile_staged.main(['--sessions',str(args.sessions),'--output',str(child),
                '--populations',str(args.population),'--batch-sizes',str(args.batch_size),
                '--session-count',str(args.session_count),'--profile-seconds','19800',*flags],emit_hook=emit)
            row=json.loads((child/'measurements.json').read_text())[0]
            if result or row['status']!='measured_full_session':raise ValueError('Complete full-session measurement required')
            rows.append(dict(mode=mode,warm=warm,measurement=row));write_json(root/'measurements.json',rows)
            if mode=='pipeline':
                for session in range(args.session_count):
                    suffix=Path(f'population_{args.population}_batch_{args.batch_size}')/f'session_{session:03d}'
                    audits.append(compare(root/'serial_warm'/suffix,child/suffix))
        before=rows[1]['measurement'];after=rows[3]['measurement']
        improvement=100*(before['elapsed_seconds']-after['elapsed_seconds'])/before['elapsed_seconds']
        report=dict(status='passed',measurements=rows,audits=audits,warm_total_improvement_percent=improvement,
                    validation_opened=False,code_hash=code_hash())
        write_json(root/'report.json',report)
        write_json(root/'status.json',dict(version='V5',status='profile_complete',stage='Full serial/pipeline audit complete',
                  mode='profile',validation_status='SEALED',started_epoch=started,execution_pass=4,execution_passes=4,
                  config=dict(population=args.population,training_sessions=args.session_count,generations=0),
                  completed_sessions=args.session_count,timing=after['timing_totals'],receipt_writer_pending=False,
                  profile_rows=[row['measurement'] for row in rows]))
        return 0
    except BaseException as error:
        failure=dict(version='V5',status='failed',stage='Full overlap timing/audit failed',error=str(error),worker_pid=os.getpid(),validation_opened=False)
        write_json(root/'failure.json',failure);write_json(root/'status.json',failure);raise
    finally:lock.unlink(missing_ok=True)


if __name__=='__main__':raise SystemExit(main())

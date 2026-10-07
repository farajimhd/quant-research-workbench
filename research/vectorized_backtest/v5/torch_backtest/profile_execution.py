"""Cold/warm specialization measurements versus immutable pre-change evidence."""
import argparse,json,os,time
from pathlib import Path
import torch
from . import profile_staged
from .runtime import require_runtime,write_json,file_hash,code_hash
from .financial_audit import audit_fills


def compare(reference,target):
    before=json.loads((reference/'receipt.json').read_text());after=json.loads((target/'receipt.json').read_text())
    keys=('day','population_sha256','execution','feature_certificate','prior_certificate',
          'identity_map_sha256','split_certificate_sha256','previous_split_certificate_sha256','profile_seconds')
    if any(before[k]!=after[k] for k in keys):raise ValueError('Before/after population, input or duration differs')
    for name,values in before['metrics'].items():
        if name=='closed_position_duration_samples':
            if values!=after['metrics'][name]:raise ValueError('Holding samples changed')
        else:
            torch.testing.assert_close(torch.tensor(after['metrics'][name],dtype=torch.float64),
                                       torch.tensor(values,dtype=torch.float64),rtol=1e-10,atol=1e-7,equal_nan=True)
    def lanes(folder,receipt):
        result={};order=receipt.get('candidate_order',list(range(len(receipt['metrics']['fill_count']))));left=0
        if sorted(order)!=list(range(len(order))):raise ValueError('Incomplete execution permutation')
        for entry in receipt['batch_receipts']:
            path=folder/entry['directory'];record=json.loads((path/'receipt.json').read_text())
            if file_hash(path/'receipt.json')!=entry['sha256'] or file_hash(path/'fills.pt')!=record['ledger_sha256']:
                raise ValueError('Actual financial receipt bytes changed')
            metrics=dict(record['metrics'])
            if receipt['profile_seconds'] not in (None,19800):
                metrics['terminal_valid']=[v==0 for v in metrics['open_quantity']]
            audit_fills(path/'fills.pt',metrics)
            saved=torch.load(path/'fills.pt',map_location='cpu',weights_only=True)
            indices=record.get('candidate_indices',order[left:left+record['candidate_count']])
            if indices!=order[left:left+record['candidate_count']]:raise ValueError('Candidate mapping changed')
            for lane,(candidate,count) in enumerate(zip(indices,saved['counts'].tolist())):
                result[candidate]=saved['ledger'][lane,:count].clone()
            left+=record['candidate_count']
        if left!=len(order) or len(result)!=len(order):raise ValueError('Incomplete candidate ledgers')
        return result
    old=lanes(reference,before);new=lanes(target,after)
    if old.keys()!=new.keys():raise ValueError('Candidate coverage changed')
    for candidate in old:torch.testing.assert_close(new[candidate],old[candidate],rtol=0,atol=0)
    return dict(actual_fills_exact=True,metrics_rtol=1e-10,metrics_atol=1e-7,
                independent_cash_fee_quantity_holding_audit=True,
                terminal_eligibility_audited=after['profile_seconds'] in (None,19800),
                reference_receipt_sha256=file_hash(reference/'receipt.json'),target_receipt_sha256=file_hash(target/'receipt.json'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline-session',type=Path,required=True);p.add_argument('--population',type=int,default=4096)
    p.add_argument('--batch-size',type=int,default=512);p.add_argument('--repeats',type=int,default=2)
    p.add_argument('--profile-seconds',type=int,default=256)
    args=p.parse_args(argv)
    if not 2<=args.repeats<=3:p.error('Bounded cold/warm sweep requires two or three passes')
    root=require_runtime(args.output);reference=args.baseline_session
    if (root/'identity.json').exists():raise ValueError('Immutable profile output already exists')
    write_json(root/'identity.json',dict(version='v5-execution-profile',code_hash=code_hash(),
        baseline_receipt_sha256=file_hash(reference/'receipt.json'),sessions_sha256=file_hash(args.sessions),
        arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}))
    lock=root/'owner.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
    rows=[];audits=[];started=time.time()
    try:
        for repeat in range(args.repeats):
            child=root/f'repeat_{repeat:03d}'
            def emit(status):
                if status.get('status')=='profile_complete' and not status.pop('parent_final',False):status['status']='profiling'
                status.update(started_epoch=started,execution_pass=repeat+1,execution_passes=args.repeats)
                status['stage']=f"{'Cold' if repeat==0 else 'Warm'} specialization | {status.get('stage','')}"
                write_json(root/'status.json',status)
                if (root/'STOP').exists():write_json(child/'STOP',dict(reason='Parent profile stop requested'))
            result=profile_staged.main(['--sessions',str(args.sessions),'--output',str(child),
                '--populations',str(args.population),'--batch-sizes',str(args.batch_size),
                '--session-count','1','--profile-seconds',str(args.profile_seconds)],emit_hook=emit)
            if result:raise ValueError('Child specialization profile failed')
            row=json.loads((child/'measurements.json').read_text())[0]
            if row['status']=='memory_limit':raise MemoryError(row['error'])
            target=child/f'population_{args.population}_batch_{args.batch_size}'/'session_000'
            audits.append(compare(reference,target));rows.append(row)
            write_json(root/'measurements.json',rows)
        baseline=json.loads((reference/'receipt.json').read_text())['timing']
        cold=rows[0]['timing_totals'];warm={key:sum(row['timing_totals'][key] for row in rows[1:])/(len(rows)-1) for key in baseline}
        improvement={key:100*(baseline[key]-warm[key])/baseline[key] if baseline[key] else None for key in baseline}
        report=dict(status='measured_not_qualification',baseline_timing=baseline,cold_timing=cold,warm_timing=warm,
                    warm_improvement_percent=improvement,measurements=rows,audits=audits,validation_opened=False)
        write_json(root/'report.json',report)
        emit(dict(version='V5',status='profile_complete',stage='Audited specialization timings complete',
                  parent_final=True,
                  validation_status='SEALED',config=dict(population=args.population,training_sessions=1,generations=0),profile_rows=rows))
        return 0
    except BaseException as error:
        failure=dict(version='V5',status='failed',stage='Specialization timing/audit failed',error=str(error),worker_pid=os.getpid(),validation_opened=False)
        write_json(root/'failure.json',failure);write_json(root/'status.json',failure);raise
    finally:lock.unlink(missing_ok=True)


if __name__=='__main__':raise SystemExit(main())

"""Read-only full-training receipt adapter for the existing terminal renderer."""
import json
import re
from pathlib import Path
from .runtime import file_hash


class FullTrainingView:
    def __init__(self):
        self.checkpoint_key=None;self.checkpoint=None;self.ranking_key=None;self.rows=[]
        self.session_stats={}
        self.batch_receipts={}

    def read(self,output):
        output=Path(output);path=output/'checkpoint.json';stat=path.stat()
        key=(stat.st_mtime_ns,stat.st_size)
        if key!=self.checkpoint_key:
            self.checkpoint=json.loads(path.read_text());self.checkpoint_key=key
        saved=self.checkpoint;contract=saved['contract']
        # The contract stores a spec fingerprint; derive coverage from sealed
        # generation receipts rather than interpreting it as session data.
        if contract['version']!='v6-all-training-search-v1':raise ValueError('Not a full-training search')
        done=saved['completed_generations'];total=contract['generations'];n=contract['population']
        status_path=output/'status.json'
        status=json.loads(status_path.read_text()) if status_path.exists() else {}
        if status.get('validation_opened',False):raise ValueError('Full-training observer cannot read validation results')
        updated=status_path.stat().st_mtime if status_path.exists() else stat.st_mtime
        if done:
            folder=output/f'generation-{done:04d}';complete=folder/'complete.json';binding=file_hash(complete)
            if binding!=saved['last_generation_sha256']:raise ValueError('Completed generation seal changed')
            if (done,binding)==self.ranking_key:
                for path,expected in self.session_stats.items():
                    s=path.stat()
                    if (s.st_mtime_ns,s.st_size)!=expected:raise ValueError('Ranking session receipt changed')
            if (done,binding)!=self.ranking_key:
                record=json.loads(complete.read_text())
                if record.get('status')!='complete' or not record.get('selection_allowed') or record.get('validation_opened',True) or len(set(record['training_days']))!=30:
                    raise ValueError('Ranking requires complete all30 training receipts')
                sessions=[];session_stats={}
                for day in record['training_days']:
                    receipt=folder/day/'receipt.json'
                    if receipt.resolve().parent.parent!=folder.resolve() or file_hash(receipt)!=record['session_receipts'][day]:raise ValueError('Ranking session receipt changed')
                    session=json.loads(receipt.read_text())
                    if session.get('day')!=day or session.get('population_sha256')!=record['population_sha256'] or session.get('candidate_indices')!=list(range(n)) or not session.get('full_session') or session.get('validation_opened',True):raise ValueError('Ranking candidate/session coverage changed')
                    sessions.append(session['metrics'])
                    s=receipt.stat();session_stats[receipt]=(s.st_mtime_ns,s.st_size)
                ranking=record['ranking'];rows=[]
                def values(name,i):return [v[name][i] for v in sessions] if all(name in v for v in sessions) else None
                def summed(name,i):
                    v=values(name,i);return sum(v) if v is not None else None
                order=sorted(range(n),key=lambda i:(not ranking['feasible'][i],-ranking['score'][i],i))
                for rank,i in enumerate(order,1):
                    metrics={k:ranking[k][i] for k in ('total_pnl','best_day_pnl','other_days_pnl','profitable_day_fraction','median_return','tail_loss','session_tail_mean_pnl','session_tail_count')}
                    metrics['objective_components']={k:v[i] for k,v in ranking['components'].items()}
                    metrics.update(worst_pnl=min(values('net_pnl',i)),worst_drawdown=max(values('drawdown',i)),
                        positions=summed('positions_opened',i),fills=summed('fill_count',i),open=summed('open_positions',i),
                        closed_positions=summed('closed_positions',i),winning_positions=summed('winning_positions',i),losing_positions=summed('losing_positions',i),
                        stop_risk_hours=summed('stop_risk_dollar_seconds',i)/3600,capital_hours=summed('capital_dollar_seconds',i)/3600)
                    closed=metrics['closed_positions'];won=metrics['winning_positions'];profit=summed('gross_profit',i);loss=summed('gross_loss',i)
                    metrics['position_win_rate']=won/closed if closed and won is not None else None
                    metrics['profit_factor']=profit/loss if loss and profit is not None else None
                    rows.append(dict(rank=rank,candidate=i+1,score=ranking['score'][i],valid=ranking['feasible'][i],metrics=metrics))
                if file_hash(complete)!=binding:raise ValueError('Generation changed during observer read')
                self.rows=rows;self.ranking_key=(done,binding);self.session_stats=session_stats
        current=output/f'generation-{min(done+1,total):04d}'
        resident=current/'resident-status.json'
        active=json.loads(resident.read_text()) if resident.exists() and done<total else {}
        if active:updated=max(updated,resident.stat().st_mtime)
        # Preparation has its own durable progress file. Read only the family
        # named by the worker, never an unrelated or previously completed one.
        restoring=re.fullmatch(r'Restoring compiler context (\d+)/(\d+); no full-session evaluation',active.get('stage',''))
        if restoring:
            family=int(restoring.group(1))
            if not 1<=family<=int(restoring.group(2)):raise ValueError('Invalid compiler restoration family')
            child=output/'compiler-priming'/f'family-{family-1:04d}'/'resident-status.json'
            if child.exists():
                preparation=json.loads(child.read_text())
                if preparation.get('validation_opened',False):raise ValueError('Compiler preparation opened validation')
                active={**active,'stage':active['stage']+' | '+preparation.get('stage','Preparing'),
                    'sessions':[],'day':preparation.get('day','')}
                updated=max(updated,child.stat().st_mtime)
        launch=output/'active.json'
        started=json.loads(launch.read_text())['creation_time'] if launch.exists() else output.stat().st_ctime
        batch_size=contract.get('evaluator',{}).get('batch_size',n)
        total_batches=30*((n+batch_size-1)//batch_size)
        completed_batches=total_batches if done==total else 0
        if done<total:
            retained={}
            for receipt in current.glob('*/batch-*/receipt.json'):
                s=receipt.stat();identity=(s.st_mtime_ns,s.st_size)
                if self.batch_receipts.get(receipt)!=identity:
                    batch=json.loads(receipt.read_text());indices=batch['candidate_indices']
                    offset=int(receipt.parent.name.removeprefix('batch-'))
                    if (batch.get('population_sha256')!=saved['population_sha256'] or
                        batch.get('day')!=receipt.parent.parent.name or offset%batch_size or not 0<=offset<n or
                        indices!=list(range(offset,min(n,offset+batch_size))) or
                        not batch.get('financial_audit_passed') or not batch.get('full_session') or
                        batch.get('validation_opened',True)):
                        raise ValueError('Invalid active batch progress receipt')
                retained[receipt]=identity;completed_batches+=1
            self.batch_receipts=retained
            if completed_batches>total_batches:raise ValueError('Active batch coverage exceeds training contract')
        samples=[]
        for generation in range(max(1,done),min(done+1,total)+1):
            timing_path=output/f'generation-{generation:04d}'/'resident-measurements.json'
            if timing_path.exists():
                samples.extend(v for v in json.loads(timing_path.read_text()) if v.get('active',0)>0 and v.get('session_timings'))
        seconds=sum(v['setup_seconds']+v['replay_and_audit_seconds'] for v in samples)
        units=sum(v['active'] for v in samples)
        per_unit=seconds/units if units else None
        remaining=total_batches-completed_batches
        replay_eta=remaining*per_unit if per_unit is not None else None
        cohort=active.get('sessions',[]);offset=active.get('candidate_offset')
        cohort_done=sum((current/day/f'batch-{offset:06d}'/'receipt.json').exists() for day in cohort) if offset is not None else 0
        processes=[dict(label='Saved / audited batches',done=completed_batches,total=total_batches)]
        if cohort:processes.append(dict(label='Resident cohort saved',done=cohort_done,total=len(cohort)))
        if restoring:processes.append(dict(label='Compiler contexts',done=int(restoring.group(1))-1,total=int(restoring.group(2))))
        return dict(status=status.get('status','running'),stage=active.get('stage','Training complete' if done==total else 'Preparing full-training generation'),
            config=dict(population=n,generations=total,training_sessions=30),completed_generations=done,
            completed_sessions=sum(1 for p in current.glob('*/receipt.json')) if done<total else 30,
            completed_batches=completed_batches,total_batches=total_batches,
            process_progress=processes,replay_eta=replay_eta,
            session_eta=(sum(v['replay_and_audit_seconds'] for v in samples)/len(samples)) if samples else None,
            average_batch_seconds=per_unit,
            messages=[dict(text='ETA estimates use measured preparation + concurrent replay/audit; saved receipts include resumed work.',timestamp='')],
            updated_epoch=updated,started_epoch=started,validation_status='SEALED',top_strategies=self.rows,
            evaluation_basis=f'Generation {done}: all30 training-session search ranking' if done else 'No completed all30 ranking yet',
            focus=', '.join(active.get('sessions',[])) or active.get('day',''),validation_opened=False)

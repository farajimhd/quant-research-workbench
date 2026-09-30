"""Audit ranking coverage and publish only a complete forward V6 train split.

Ignored entries are removed by recompiling the hypothetical account path,
not by deleting loss rows from a portfolio containing impossible holdings.
Feature banks and original teacher artifacts are immutable and reused.
"""
import argparse
from dataclasses import asdict
from datetime import date
import json
import math
import os
from pathlib import Path
import numpy as np
import polars as pl
from research.rl_trading.v1.common import file_hash, digest
from research.rl_trading.v6.market_attention import MarketAttentionConfig, VolumeRanker
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.split import CONTEXT_ONLY, TRAIN, DEVELOPMENT
from research.rl_trading.v6.teacher_data import load_teacher
from research.rl_trading.v6.teacher_trajectory import bind_intents, compile_trajectory
from research.rl_trading.v6.build_price_action_teacher import _decisions, _outcomes
from research.rl_trading.v6.build_price_action_geometry import source_positions

VERSION = 'rl-trading-v6-audited-training-split-1'


def coverage(session, decisions, ranks, sort_secs):
    n = len(session.listings)
    entry = {}
    for item in decisions:
        if 1<=item.token<=n:
            entry.setdefault(item.close_us,[]).append(item.token-1)
    rankers = {r:VolumeRanker(n,MarketAttentionConfig(top_r=r,sort_secs=sort_secs)) for r in ranks}
    counts = {r:0 for r in ranks}
    missed = {r:[] for r in ranks}
    total = sum(map(len,entry.values()))
    for event in session.candle_events():
        scalar = np.asarray(session.bank.scalar[event.bank_row])
        for r,ranker in rankers.items():
            ranker.observe(event.close_us,event.listing_index,scalar)
            selected = set(ranker.select(event.close_us).tolist())
            for listing in entry.get(event.close_us,()):
                if listing in selected:
                    counts[r] += 1
                else:
                    missed[r].append({'close_us':event.close_us,'listing_index':listing})
    if not total:
        raise ValueError('No original teacher entries to audit')
    return {'original_teacher_entries':total,'coverage':{
        str(r):{'covered':counts[r],'ignored':total-counts[r],
                'fraction':counts[r]/total,'outside_entries':missed[r]} for r in ranks}}


def choose_rank(reports, ranks, minimum):
    if not reports or not 0 < minimum <= 1:
        raise ValueError('Missing coverage reports or invalid acceptance threshold')
    for rank in sorted(ranks):
        if all(report['coverage'][str(rank)]['fraction'] >= minimum for report in reports):
            return rank
    raise ValueError('No candidate R satisfies per-day teacher-entry coverage; review audit and increase R')


def _write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True),encoding='utf-8')
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank-manifest',type=Path,required=True)
    parser.add_argument('--teacher-campaign',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ranks',type=int,nargs='+',default=[500,1000,2000])
    parser.add_argument('--sort-secs',type=int,default=1)
    parser.add_argument('--minimum-entry-coverage',type=float,default=.99)
    parser.add_argument('--allocation-root',action='append',default=[])
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT','')).resolve()
    output = args.output.resolve()
    if not runtime.is_dir() or any(not p.resolve().is_relative_to(runtime)
            for p in (args.bank_manifest,args.teacher_campaign,args.output)):
        raise ValueError('Audit inputs/output must be under configured runtime')
    if not 0<args.minimum_entry_coverage<=1 or any(r<1 for r in args.ranks):
        raise ValueError('Invalid coverage policy')
    allocations = {date.fromisoformat(k):Path(v) for k,v in (x.split('=',1) for x in args.allocation_root)}
    roots = json.loads(args.bank_manifest.read_text())
    if roots.get('version')!='rl-trading-v6-forward-candle-day-roots':
        raise ValueError('Unknown bank manifest contract')
    reports,entries,missing = [],[],[]
    previous = CONTEXT_ONLY[0]
    for day in TRAIN+DEVELOPMENT:
        root = roots['day_roots'].get(str(day))
        teacher = args.teacher_campaign/'teacher'/str(day)
        if root is None or not (teacher/'complete.json').is_file():
            missing.append(str(day))
            previous = day
            continue
        prior = roots['day_roots'].get(str(previous))
        if prior is None:
            raise ValueError('Audit prior-day context absent')
        teacher_cert = json.loads((teacher/'complete.json').read_text())
        for name in ('decisions','outcomes','positions'):
            if file_hash(teacher/f'{name}.parquet')!=teacher_cert[f'{name}_sha256']:
                raise ValueError('Original teacher file changed before coverage audit')
        for key in ('unresolved_positions','pending_entries','pending_exits'):
            if teacher_cert.get(key)!=0:
                raise ValueError('Original teacher has unresolved account state')
        ledger = pl.read_parquet(teacher/'positions.parquet')
        if (file_hash(teacher/'positions.parquet')!=teacher_cert['positions_sha256'] or
                ledger.height!=teacher_cert['completed_positions'] or
                not math.isclose(float(ledger['net_pnl'].sum()),teacher_cert['modeled_net_pnl'],abs_tol=1e-5)):
            raise ValueError('Teacher position ledger does not reconcile')
        binding = {'bank_certificate':file_hash(Path(root)/'complete.json'),
                   'teacher_certificate':file_hash(teacher/'complete.json'),
                   'ranks':args.ranks,'sort_secs':args.sort_secs}
        report_path = output/'coverage'/str(day)/'complete.json'
        if report_path.is_file():
            saved = json.loads(report_path.read_text())
            if saved.get('binding')!=binding:
                raise ValueError('Existing coverage audit belongs to another source/configuration')
            report = saved['report']
        else:
            session = open_session(Path(root),runtime_root=runtime,previous_root=Path(prior))
            decisions,_ = load_teacher(teacher,session,runtime_root=runtime,audit_development=True)
            report = coverage(session,decisions,args.ranks,args.sort_secs)
            report.update(original_teacher_modeled_net=teacher_cert['modeled_net_pnl'],
                          original_teacher_positions=teacher_cert['completed_positions'],
                          original_teacher_fees=teacher_cert['modeled_fees'])
            _write_json(report_path,{'status':'coverage_audited','binding':binding,'report':report})
            del session
        reports.append((day,report))
        entries.append({'day':str(day),'role':'train' if day in TRAIN else 'development',
            'bank_root':str(root),'previous_root':str(prior),'original_teacher_root':str(teacher),
            'original_teacher_sha256':binding['teacher_certificate'],
            'bank_certificate_sha256':binding['bank_certificate'],
            'coverage_certificate':str(report_path),'coverage_sha256':file_hash(report_path)})
        previous = day
        print(json.dumps({'day':str(day),'coverage':{r:{k:v for k,v in row.items() if k!='outside_entries'}
              for r,row in report['coverage'].items()}}),flush=True)
    if missing:
        _write_json(output/'audit-state.json',{'status':'blocked_incomplete_data','missing':missing,
                                              'audited_days':[str(d) for d,_ in reports]})
        print(json.dumps({'status':'blocked_incomplete_data','missing':missing}),flush=True)
        return 0
    rank = choose_rank([r for d,r in reports if d in TRAIN],args.ranks,args.minimum_entry_coverage)
    ranking = MarketAttentionConfig(top_r=rank,sort_secs=args.sort_secs)
    for entry in entries:
        day = date.fromisoformat(entry['day'])
        source = Path(entry['bank_root'])
        derived = output/'ranked_teacher'/str(rank)/str(day)
        expected = {'original_teacher_sha256':entry['original_teacher_sha256'],
                    'ranking':asdict(ranking),'bank_certificate_sha256':entry['bank_certificate_sha256']}
        if (derived/'complete.json').is_file():
            saved = json.loads((derived/'complete.json').read_text())
            if any(saved.get(k)!=v for k,v in expected.items()) or any(
                file_hash(derived/f'{name}.parquet')!=saved[f'{name}_sha256'] for name in ('decisions','outcomes','positions')):
                raise ValueError('Prepared ranked teacher changed')
        else:
            session = open_session(source,runtime_root=runtime,previous_root=Path(entry['previous_root']))
            original = Path(entry['original_teacher_root'])
            original_cert = json.loads((original/'complete.json').read_text())
            _,_,allocation_hash = source_positions(source,day,allocation_root=allocations.get(day))
            allocation_root = allocations.get(day,source)
            bracket_root = args.teacher_campaign/'brackets'/str(day)
            bracket_cert = json.loads((bracket_root/'complete.json').read_text())
            if (file_hash(bracket_root/'complete.json')!=original_cert['bracket_certificate_sha256'] or
                file_hash(bracket_root/'oracle_brackets.parquet')!=bracket_cert['brackets_sha256'] or
                allocation_hash!=original_cert['allocation_sha256']):
                raise ValueError('Ranked recompile source geometry changed')
            intents,_ = bind_intents(pl.read_parquet(allocation_root/'intended_allocations.parquet'),
                pl.read_parquet(bracket_root/'oracle_brackets.parquet'),session.listings)
            accepted = set(pl.read_parquet(original/'positions.parquet')['episode_uid'])
            intents = tuple(i for i in intents if i.episode_uid in accepted)
            decisions,outcomes,report = compile_trajectory(session,intents,ranking_config=ranking)
            ledger = report.pop('ledger')
            if any(report[k] for k in ('unresolved_positions','pending_entries','pending_exits')) or not math.isclose(
                    sum(r['net_pnl'] for r in ledger),report['modeled_net_pnl'],abs_tol=1e-5):
                raise ValueError('Ranked teacher failed account reconciliation')
            derived.mkdir(parents=True,exist_ok=True)
            if not ledger:
                raise ValueError('Ranking eliminated all trainable positions')
            saved = {**original_cert,**report,**expected,'rank_exclusion_policy':'ignore_entire_entry_and_recompile_account',
                     'coverage_certificate_sha256':entry['coverage_sha256']}
            for name,frame in (('decisions',_decisions(decisions)),('outcomes',_outcomes(outcomes)),('positions',pl.DataFrame(ledger))):
                path = derived/f'{name}.parquet'
                frame.write_parquet(path,compression='zstd')
                saved[f'{name}_sha256'] = file_hash(path)
            _write_json(derived/'complete.json',saved)
            del session
        entry.update(teacher_root=str(derived),teacher_sha256=file_hash(derived/'complete.json'))
    certificate = {'version':VERSION,'status':'audited_ready_for_training','ranking':asdict(ranking),
        'minimum_entry_coverage':args.minimum_entry_coverage,'rank_exclusion_policy':'ignore_entire_entry_and_recompile_account',
        'days':entries,'sealed_test_accessed':False,
        'model_audit_required':'causality_and_real_rollout_reconstruction_smoke'}
    certificate['hash'] = digest(certificate)
    _write_json(output/'complete.json',certificate)
    _write_json(output/'audit-state.json',{'status':'audited_ready_for_training','selected_r':rank})
    print(json.dumps({'status':certificate['status'],'selected_r':rank}),flush=True)
    return 0


if __name__=='__main__':
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    raise SystemExit(main())

"""Audit causal base and recovery gates, including an explicitly bounded live prefix."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
import sqlite3
from zoneinfo import ZoneInfo
from src.trading_runtime import historical_hod as H, v7_setup as V
from strategy_222_supervised_research import digest,save
from strategy_222_feature_comparison import features


def decision_rows(connection, cutoff):
    """Compare instants, not ISO text with potentially different UTC offsets."""
    return connection.execute("select sequence,event_time,payload_json from journal where category='strategy_decision' and julianday(event_time)<=julianday(?) order by sequence", (cutoff,))


def label_bases(rows,settings,tick,ledger_path):
    """Future quotes label a recorded support stop; they never construct it."""
    import numpy as np
    from strategy_222_supervised_research import hindsight_label
    ledger=json.loads(ledger_path.read_text());cache={};hashes={};result=[]
    for row in rows:
        at=datetime.fromisoformat(row['time']).timestamp()
        stop=H.stop_below(row['support_lower'],settings,tick)
        windows=[(key,value) for key,value in ledger.items() if value['window']['symbol']==row['symbol']
            and datetime.fromisoformat(value['window']['start']).timestamp()<=at
            <=datetime.fromisoformat(value['window']['end']).timestamp()]
        label={'valid':False,'reason':'outside_frozen_quote_windows'}
        window=None
        if windows:
            window,authority=max(windows,key=lambda item:(item[1]['window']['end'],item[0]))
            if authority['status']!='completed' or not authority['source_revision']['complete_for_history']:
                raise ValueError('Incomplete quote authority')
            if window not in cache:
                path=ledger_path.parent/f'quotes-{window}.npz';hashes[window]=digest(path)
                if hashes[window]!=authority['sha256']:raise ValueError('Quote source changed')
                with np.load(path) as archive:cache[window]=archive['data']
                if not len(cache[window]) or (np.diff(cache[window][:,0])<0).any():raise ValueError('Empty or unordered quotes')
            label=hindsight_label(cache[window],at,stop)
        result.append(dict(sequence=row['sequence'],time=row['time'],symbol=row['symbol'],
            first_blocker=row['first_blocker'],recovery_reason=row['recovery_reason'],
            recorded_liquidity_failed=row.get('recorded_liquidity_failed'),spread_bps=row.get('spread_bps'),
            stop=stop,window=window,label=label))
    return result,hashes


def recovery_audit(metadata,at,settings):
    base=metadata.get('research_base_assessment') or {}
    if base.get('status')!='measured':return {'status':'unavailable','reason':base.get('reason','assessment_missing')}
    if base['observed_at']!=at:raise ValueError('Base assessment clock mismatch')
    if not base['checks'] or any(type(v) is not bool for v in base['checks'].values()):
        raise ValueError('Invalid base checks')
    if not all(base['checks'].values()):return {'status':'base_failed','failed':base['failed']}
    swing=base['swing'];prior=(metadata.get('setup_recovery') or {}).get('last_exit')
    if (swing['confirmed_at']>at or swing['pivot_at']>swing['confirmed_at']
            or base['range']['end']>at or prior and prior['at']>at):
        raise ValueError('Future recovery operand')
    if settings['setup_recovery_enabled'] and 'setup_recovery' not in metadata:
        raise ValueError('Missing recovery authority')
    local=datetime.fromtimestamp(at,ZoneInfo('America/New_York'))
    regular_start=(local.replace(hour=9,minute=30,second=0,microsecond=0).timestamp()
        if settings['setup_recovery_regular_base'] and (9,30)<=(local.hour,local.minute)<(16,0) else 0.)
    reason,phase=V.recovery_permission({'last_exit':prior,'range':base['range']},swing,
        {'bar':{'close':metadata['reference_price'],'end':at}},
        stop_gain_guard=bool(settings['setup_recovery_stop_gain_guard']),
        tight_base=bool(settings['setup_base_recovery_maximum_range_pct'] and
            base['range_pct']<=settings['setup_base_recovery_maximum_range_pct']),
        unprotected_reentry=bool(settings['setup_recovery_unprotected_reentry']),
        entry_reclaim=bool(settings['setup_recovery_entry_reclaim']),regular_session_start=regular_start,
        regular_full_range=bool(settings['setup_recovery_regular_full_range'])) if settings['setup_recovery_enabled'] else ('','building')
    return dict(status='base_passed',recovery_reason=reason,recovery_phase=phase,
        price=metadata['reference_price'],risk_pct=base['risk_pct'],range_pct=base['range_pct'],
        support_lower=swing['lower'],support_confirmed_at=swing['confirmed_at'],
        recorded_liquidity_failed=(metadata.get('liquidity_admission') or {}).get('failed'),
        spread_bps=((metadata.get('liquidity_admission') or {}).get('facts') or {}).get('spread_bps'))


def run(manifest_path,output,cutoff=None,quote_ledger=None):
    output=output.resolve();output.relative_to(Path('D:/TradingML/runtimes').resolve())
    manifest=json.loads(manifest_path.read_text());trials=manifest['trials']
    if len(trials)!=1:raise ValueError('Select a single trial manifest')
    trial=trials[0]
    root=Path(__file__).resolve().parents[1]
    for name in ('src/trading_runtime/historical_hod.py','src/trading_runtime/v7_setup.py'):
        if digest(root/name)!=manifest['identity']['source'][name.replace('/','\\')]:
            raise ValueError('Recovery source differs from recorded replay')
    run_root=Path('D:/TradingML/runtimes/trading/backtest')/trial['run_id']
    config_path=run_root/'approved-configuration.json'
    parameters=json.loads(config_path.read_text())['payload']['strategy']['parameters']
    settings=dict(H.DEFAULTS,**parameters['historical_hod'])
    if not settings['setup_base_diagnostics_enabled']:raise ValueError('Diagnostics were not enabled')
    journal=run_root/'journal.sqlite3'
    if cutoff is None:
        wal=Path(str(journal)+'-wal')
        if trial['status']!='completed' or wal.exists() and wal.stat().st_size:raise ValueError('Full audit requires a closed completed journal')
    else:
        parsed=datetime.fromisoformat(cutoff)
        if parsed.tzinfo is None:raise ValueError('Cutoff must include timezone')
        cutoff=parsed.astimezone(timezone.utc).isoformat()
    c=sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True);c.execute('BEGIN')
    try:
        last=c.execute("select max(event_time) from journal where category='strategy_decision'").fetchone()[0]
        if cutoff and datetime.fromisoformat(cutoff)>datetime.fromisoformat(last):raise ValueError('Cutoff exceeds committed decisions')
        maximum_sequence=c.execute('select max(sequence) from journal').fetchone()[0]
        counts=Counter();first=Counter();combinations=Counter();rows=[]
        unavailable=Counter();failed_checks=Counter()
        for sequence,stamp,raw in decision_rows(c,cutoff or last):
            d=json.loads(raw);m=d.get('metadata') or {}
            if m.get('status') not in ('watching','reentry_cooldown'):continue
            if not any(str(x).startswith(f"qmd-derived:{d['ticker']}:1s:") for x in d.get('source_signal_ids',[])):continue
            result=recovery_audit(m,datetime.fromisoformat(stamp).timestamp(),settings)
            counts[result['status']]+=1
            if result['status']=='unavailable':unavailable[result['reason']]+=1
            if result['status']=='base_failed':failed_checks.update(result['failed'])
            if result['status']=='base_passed':
                first[d['reason']]+=1;combinations[(d['reason'],result['recovery_reason'] or 'recovery_passed')]+=1
                rows.append(dict(sequence=sequence,time=stamp,symbol=d['ticker'],first_blocker=d['reason'],
                    features=features(m,datetime.fromisoformat(stamp).timestamp()),**result))
    finally:c.close()
    report=dict(status='prefix' if cutoff else 'completed',run_id=trial['run_id'],cutoff=cutoff or last,
        transaction_max_sequence=maximum_sequence,counts=dict(counts),unavailable_reasons=dict(unavailable),
        base_failed_checks=dict(failed_checks),first_blockers_with_passing_base=dict(first),
        gate_combinations=[dict(first_blocker=a,recovery_reason=b,count=n) for (a,b),n in sorted(combinations.items())],
        rows=rows,inputs={str(p):digest(p) for p in (manifest_path,config_path,Path(__file__),
            Path(__file__).with_name('strategy_222_feature_comparison.py'),root/'src/trading_runtime/v7_setup.py')},
        method='Flat-state 1s decisions only. Evaluate recorded measured base geometry, then the shared recovery function with recorded last exit and configured switches. Selected support already passed retired-support filtering in recovery_observe. No outcome labels, future price, ticker-specific rules, or gate overrides. Other entry and execution gates are not certified by a passing base/recovery assessment. Counts are dependent decisions, not opportunities or trades.')
    if not cutoff:report['journal_sha256']=digest(journal)
    if quote_ledger:
        labels,hashes=label_bases(rows,settings,parameters['execution']['tick_size'],quote_ledger)
        report['support_stop_labels']=labels
        report['label_sources']={'ledger_sha256':digest(quote_ledger),'quote_sha256':hashes,
            'helper_sha256':digest(Path(__file__).with_name('strategy_222_supervised_research.py'))}
        report['label_policy']='Hypothetical 100-share entry after100ms,5bps slippage per side, minimum$1 fee per side, fixed recorded-support stop and2R target within300seconds. Stop uses exact configured buffer/tick and shared stop_below function. Quotes are future labels only. This is not an OMS replay: actual order ceilings, fills, sizing, position changes and later management differ. Overlapping row results must not be summed as portfolio profit.'
        totals=Counter()
        for r in labels:
            l=r['label'];totals['valid' if l['valid'] else 'invalid']+=1
            if l['valid']:totals['profitable' if l['profitable'] else 'nonprofitable']+=1
            else:totals['invalid:'+l['reason']]+=1
        report['label_counts']=dict(totals)
    save(output,report)
    print(f"Audit {report['status']}: {sum(counts.values())} decisions, {counts['base_passed']} passing bases; output={output}",flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--cutoff',help='Timezone-aware cutoff for a consistent live-journal prefix')
    parser.add_argument('--quote-ledger',type=Path,help='Optionally add separate hindsight labels using recorded support stops')
    args=parser.parse_args();run(args.manifest,args.output,args.cutoff,args.quote_ledger)

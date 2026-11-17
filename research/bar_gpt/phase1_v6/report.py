"""Reproduce the campaign comparison from sealed run and finalist artifacts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from ..v5.shards.phase1_contract import write_json


def read(path):return json.loads(path.read_text())


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--recommended',required=True);a=p.parse_args()
    root=a.root;ledger=read(root/'campaign.json');dataset=read(root/'dataset/manifest.json')
    rows=[]
    for entry in ledger['experiments']:
        run=root/entry['name'];result=read(run/'results.json');manifest=read(run/'manifest.json');profile=read(run/'profile.json')
        full=result['evaluations']['development_full'];masked=result['evaluations']['development_masked']
        rows.append(dict(name=entry['name'],eligible=not entry['name'].startswith('e04-'),hypothesis=manifest['hypothesis'],
            parent=manifest['parent'],source_revision=manifest['source_revision'],parameters=manifest['parameters'],config=manifest['config'],
            price_full=full['balanced_price_relative_mean'],price_masked=masked['balanced_price_relative_mean'],
            activity_full=full['balanced_activity_log_mae'],activity_masked=masked['balanced_activity_log_mae'],
            worst_group=full['worst_price_band_relative_mean'],selection_score=full['selection_score'],
            seconds=result['elapsed_seconds'],process_wall_seconds=entry['elapsed'],gpu_peak_gib=result['gpu_peak_bytes']/2**30,
            startup_seconds=read(run/'startup.json')['seconds'],evaluation_seconds=result['evaluation_seconds'],profile=profile,
            profile_derived_training_seconds=manifest['steps']*profile['selected_batch']/max(x['windows_per_second'] for x in profile['configurations']),
            probe_r2={k:v['r2'] for k,v in result['probes'].items()},checkpoint_sha256=result['checkpoint_sha256']))
    audit_root=root/'finalist-audit';completion=read(audit_root/'complete.json')
    audits={name:read(audit_root/(name+'.json')) for name in completion['names']}
    winner=audits[a.recommended]
    summary=dict(recommended=a.recommended,experiments=rows,finalists=completion['names'],dataset_hash=dataset['hash'],
        audit={name:dict(full=value['metrics']['audit_full'],masked=value['metrics']['audit_masked'],probes=value['audit_probes'],
            untrained=value['untrained_audit_probes'],raw_summary=value['raw_summary_audit_probes'],controls=value['audit_controls'],
            representation=value['representation']) for name,value in audits.items()})
    write_json(root/'comparison.json',summary)
    lines=['BarGPT bounded encoder campaign — reproducible comparison','',
        'Recommendation (research candidate, not a trading/production certification): '+a.recommended,
        'Dataset hash: '+dataset['hash'],'Original chronological split hash: '+dataset['source_split_hash'],
        'Source receipts/payloads verified: '+str(dataset['verified_source_files']),
        'Preparation seconds: '+str(dataset['elapsed_seconds']),
        'Weights: uniform selected observations for calibration/metrics; equal training mass per populated timeframe/last-close-price-band cell.',
        'Fixed training-only log(price)/log1p(activity) parameters; FP32 model/readout and FP64 inverse. No target-derived inverse inputs.',
        'All dollar/quantity errors use stored split-adjusted shard units; original source rows, masks and clocks are unchanged.',
        'Held-out audit: one pass for two frozen finalists; no future-task labels or trading-usefulness claim.','',
        'DEVELOPMENT COMPARISON (relative price errors are percentages; activity is log1p MAE)',
        'experiment | full price % | masked price % | activity full | activity masked | worst group % | seconds | parameters']
    for row in rows:
        lines.append(f"{row['name']} | {100*row['price_full']:.4f} | {100*row['price_masked']:.4f} | {row['activity_full']:.5f} | {row['activity_masked']:.5f} | {100*row['worst_group']:.3f} | {row['seconds']:.1f} | {row['parameters']:,}"+(' | EXCLUDED: source-revision typo' if not row['eligible'] else ''))
    lines+=['','PROVENANCE AND SCIENTIFIC LIMITS',
        'E04 is preserved but excluded because its launch revision string was incorrect. E05 is its verified repeat. Admission now verifies the full deployment receipt and source file hashes.',
        'Historical V5 raw baseline is a same-evaluation-population comparison, not a matched transform-only ablation: data exposure, architecture, objective and precision differ.',
        'Cheap one-minute trade-close groups are absent from development/audit discovery; the full feature metrics still retain a rare below-$1 bid-low observation.',
        'Sparse quote coverage is limited. All observed difficult values remain in metrics. Empty groups and denominator counts are explicit in JSON.',
        'This is a stratified research subset, not an unbiased market-population estimate. A single scientific seed was used; no multi-seed robustness claim.',
        'Scale and optional validity slots preserve input information architecturally; near-perfect scale/coverage probes must not be credited solely to learned optimization.',
        'The scalar checkpoint criterion includes the worst price-band group, which can contain only one valid observation; inspect broad groups and frozen probes alongside it.',
        'CPU checkpoint-resume parity passed exactly. GPU resumes were not needed; GPU bitwise reproducibility was not established.','',
        'HELD-OUT AUDIT']
    for name,value in audits.items():
        lines.append(name)
        for mode in ('audit_full','audit_masked'):
            m=value['metrics'][mode];nonfinite=sum(v['nonfinite'] for v in m['frames'].values());negative=sum(v['negative'] for v in m['frames'].values())
            violations=sum(s['prediction'] for v in m['frames'].values() for s in v['structure'].values())
            lines.append(f"  {mode}: price={100*m['balanced_price_relative_mean']:.4f}%; activity_log_MAE={m['balanced_activity_log_mae']:.5f}; worst_group={100*m['worst_price_band_relative_mean']:.3f}%; nonfinite={nonfinite}; negative={negative}; structure_violations={violations}")
        lines.append('  Frozen probes: name | trained R2 | untrained R2 | raw-summary R2')
        for key,v in value['audit_probes'].items():lines.append(f"  {key} | {v['r2']:.6f} | {value['untrained_audit_probes'][key]['r2']:.6f} | {value['raw_summary_audit_probes'][key]['r2']:.6f}")
        for key,v in value['audit_controls'].items():lines.append(f"  {key}: price={100*v['balanced_price_relative_mean']:.4f}%; activity_log_MAE={v['balanced_activity_log_mae']:.5f}")
        lines.append('  Effective embedding rank: '+str(value['representation']['effective_rank']))
    lines+=['','RECOMMENDED CHECKPOINT AND ENCODER',
        str(audit_root/(a.recommended+'-pinned.pt')),'Checkpoint SHA-256: '+winner['checkpoint_sha256'],
        str(audit_root/(a.recommended+'-encoder-only.pt')),
        'Encoder SHA-256: '+read(audit_root/(a.recommended+'-export.json'))['sha256'],
        'Training source revision: '+winner['identity']['source_revision'],
        'Selected checkpoint step: '+str(winner['selected_step']),
        'Independent encoder loading: from research.bar_gpt.phase1_v6.export import load_encoder',
        'encoder = load_encoder(ENCODER_PATH); z = encoder.encode_raw(raw, source_valid, end_us, timeframe_us)',
        'The encoder-only artifact contains no decoder parameters; raw-input output parity was checked against the trained model.','',
        'ALL 18 CHANNELS, EACH TIMEFRAME: RECOMMENDED AUDIT RAW-UNIT MAE',
        'timeframe_us | channel | full_MAE | masked_MAE | full relative mean/median/P90 (prices) or log1p MAE (activity) | zero/tiny denominator counts']
    for frame,group in winner['metrics']['audit_full']['frames'].items():
        for channel,v in group['channels'].items():
            masked=winner['metrics']['audit_masked']['frames'][frame]['channels'][channel]
            relative=v.get('relative')
            detail=(f"{100*relative['mean']:.5g}%/{100*relative['median']:.5g}%/{100*relative['p90']:.5g}%" if relative else f"log1p={v['log1p_absolute_error'].get('mean',float('nan')):.5g}")
            lines.append(f"{frame} | {channel} | {v['mae']:.7g} | {masked['mae']:.7g} | {detail} | {v['zero_denominators']}/{v['tiny_denominators']}")
    lines+=['','Detailed per-band distributions, masks, variance, structure counts, learning curves, profiles and hashes are in comparison.json and immutable per-run/finalist JSON files.']
    for filename,title in (('campaign-review.json','RESEARCH CONCLUSIONS AND DISPOSITIONS'),('next-training-proposal.json','NEXT FULL-TRAINING CONFIGURATION FOR APPROVAL')):
        if (root/filename).exists():lines+=['',title,json.dumps(read(root/filename),indent=2)]
    (root/'comparison.txt').write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()

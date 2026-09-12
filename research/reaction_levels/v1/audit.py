"""Post-fit reporting only: no model selection, refitting or probability changes."""
from pathlib import Path
import json
from hashlib import sha256
import numpy as np
import pandas as pd
from .config import CONTRACT
from .model import metrics
from .source import write_json


def frequency_baseline(calibration_counts):
    counts=np.asarray(calibration_counts,dtype=float)
    if counts.shape!=(4,) or (counts<0).any() or not np.isfinite(counts).all():
        raise ValueError('Invalid calibration counts')
    return (counts+1)/(counts.sum()+4)


def run(root):
    root=Path(root);model=json.loads((root/'model-manifest.json').read_text())
    manifest=json.loads((root/'manifest.json').read_text())
    evaluation=json.loads((root/'evaluation.json').read_text())
    model_hash=sha256((root/'model.joblib').read_bytes()).hexdigest()
    if model_hash!=model['model_hash'] or model_hash!=evaluation['model_hash']:raise ValueError('Model provenance mismatch')
    frame=pd.read_parquet(root/'test-predictions.parquet');names=['p_'+s for s in CONTRACT['labels']]
    if frame.duplicated(['t','target_upper']).any():raise ValueError('Duplicate inference rows')
    probabilities=frame[names].to_numpy()
    if not np.isfinite(probabilities).all() or (probabilities<0).any() or not np.allclose(probabilities.sum(axis=1),1):raise ValueError('Invalid model probabilities')
    valid=frame.label>=0;scored=frame[valid]
    if not ((scored.label_end>scored.t)&(scored.label_end<=scored.t+60)).all():raise ValueError('Label horizon drift')
    # Baseline depends only on calibration-period counts frozen before the test.
    prior=frequency_baseline(model['calibration_metrics']['class_counts'])
    baseline=metrics(scored.label,np.tile(prior,(len(scored),1)))
    minute=scored[(scored.t.astype('int64')%60)==0]
    sparse=metrics(minute.label,minute[names].to_numpy())
    sparse_baseline=metrics(minute.label,np.tile(prior,(len(minute),1)))
    audit=dict(model_hash=model_hash,calibration_frequency_baseline=baseline,
               one_per_minute_per_target=sparse,one_per_minute_baseline=sparse_baseline,
               label_order=CONTRACT['labels'],audit_source_hash=sha256(Path(__file__).read_bytes()).hexdigest(),
               predictions_hash=sha256((root/'test-predictions.parquet').read_bytes()).hexdigest(),
               notes=['Touch timestamps are not independent encounters, especially while inside a band.',
                      'One-minute sampling removes same-side overlapping horizons, not cross-side or market dependence.',
                      'This report changes neither the fitted model nor its probabilities.'])
    write_json(root/'evaluation-audit.json',audit)
    parts=[json.loads(p.read_text()) for p in (root/'partitions').glob('*.json')]
    prep=sum(p['timings']['total_seconds'] for p in parts)
    report=[f"# {manifest['ticker']}: {evaluation['test_day']} reaction-model test",'',
            f"Fit {model['fit_days'][0]} through {model['fit_days'][-1]} ({len(model['fit_days'])} sessions, {model['fit_rows']:,} forecasts); calibrate {model['calibration_days'][0]} through {model['calibration_days'][-1]} ({len(model['calibration_days'])} sessions, {model['calibration_rows']:,} forecasts). The test date was excluded from both.",'',
            f"The {manifest['seed_day']} seed book was updated after every session. Test predictions use only the previous finalized checkpoint. Scored {len(scored):,} of {len(frame):,} target forecasts; {int((~valid).sum()):,} censored.",'',
            '| Metric | Model | Recent-frequency baseline |','|---|---:|---:|']
    for key in ('log_loss','brier','accuracy','balanced_accuracy','resolved_contact_log_loss'):
        report.append(f"| {key} | {evaluation['test'][key]:.5f} | {baseline[key]:.5f} |")
    report+=['', 'Lower log loss/Brier is better. The recent baseline uses only calibration-period frequencies. The original training-frequency baseline is retained in evaluation.json; it can be weaker when book coverage and outcome frequencies shift.',
             '',f"At one forecast per minute per side: {len(minute)} rows, log loss {sparse['log_loss']:.5f} versus {sparse_baseline['log_loss']:.5f}; accuracy {sparse['accuracy']:.1%}. These remain correlated across sides and market conditions.",'',
             f"Preparation of {len(parts)} labeled sessions: {prep/60:.2f} min (excluding seed and any superseded attempts). Tree fit: {model['fit_seconds']:.2f}s. Calibration: {model['calibration_seconds']:.2f}s.",
             f"Single-row model inference median: {evaluation['single_row_model_latency_ms']['median']:.2f}ms; p95: {evaluation['single_row_model_latency_ms']['p95']:.2f}ms. This excludes feature construction and transport.",'',
             f"Evaluation rows by period: { {k:v['rows'] for k,v in evaluation['by_session_period'].items()} }. Sparse periods cannot support performance conclusions. These metrics do not establish trading profitability.",
             'A held-out fit date is not necessarily an untouched research holdout: the initial AAPL August 21 test was visually inspected in earlier level research. This audit does not adjust model parameters or predictions.',
             'The classifier uses causal rolling price/volume/quote features and frozen historical evidence. It does not yet consume a live structural detector or update level statistics during the day.',
             '', '[Full audit](evaluation-audit.json) | [Original evaluation](evaluation.json) | [Model manifest](model-manifest.json)']
    (root/'report.md').write_text('\n'.join(report),encoding='utf-8')
    return audit

"""Fit full admitted TRAIN candle banks and split-adjusted 120-candle tails.

SELECT-only artifact reads, no label-row reads, no sealed sources, no training.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.feature_normalization import fit_normalization


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();output=args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or output.exists():raise ValueError('Fresh laptop runtime required')
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    from research.rl_trading.v6 import saved_label_audit as source
    from research.rl_trading.v6.session_data import open_session
    active,dataset=source.published();entries=[e for e in dataset['days'] if e['role']=='train']
    if not entries:raise ValueError('No admitted training banks')
    output.mkdir()
    def sessions():
        for entry in entries:
            print('Authenticating full TRAIN bank:',entry['day'],flush=True)
            yield open_session(source.mapped(entry['bank_root']),runtime_root=source.runtime(),previous_root=source.mapped(entry['previous_root']))
    proof=fit_normalization(sessions(),dataset_sha256=active['sha256'],include_context=True)
    if proof['training_bank_certificates']!={e['day']:e['bank_certificate_sha256'] for e in entries}:raise ValueError('Training bank coverage changed')
    path=output/'normalization.json';path.write_text(json.dumps(proof,indent=2),encoding='utf-8')
    (output/'complete.json').write_text(json.dumps(dict(status='completed',sha256=file_hash(path),dataset_sha256=active['sha256'],
        training_days=[e['day'] for e in entries],sealed_sources_read=False,label_rows_read=False),indent=2))
    print('Completed full training-only candle normalization',path,flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())

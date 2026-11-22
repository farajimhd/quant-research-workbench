"""Compare complete population receipts without rerunning model inference."""
import argparse
import json
from pathlib import Path
from ..v5.shards.phase1_contract import file_hash, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--reference', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    def read(path):
        data = json.loads(path.read_text())
        if not data.get('full_population'):
            raise ValueError(f'Not a completed full-population receipt: {path}')
        return data

    candidate = read(args.candidate)
    result = {'candidate': str(args.candidate),
              'candidate_receipt_sha': file_hash(args.candidate),
              'checkpoint_sha': candidate['checkpoint_sha'], 'comparisons': []}
    for path in args.reference:
        reference = read(path)
        if candidate['windows'] != reference['windows']:
            raise ValueError('Population counts differ; compare provenance first')
        current, previous = candidate['metrics'], reference['metrics']
        ratios = sorted(((v / previous[k], k) for k, v in current.items()
                         if k.startswith(('all_visible/raw_mae/', 'masked/raw_mae/'))
                         and previous.get(k, 0) > 0), reverse=True)
        accuracy = sorted(((v - previous[k], k) for k, v in current.items()
                           if k in previous and k.endswith(
                               ('moving_direction_pct', 'bin_accuracy_pct'))))
        result['comparisons'].append({
            'reference': str(path), 'reference_receipt_sha': file_hash(path),
            'mae_groups': len(ratios),
            'mae_groups_improved': sum(r < 1 for r, _ in ratios),
            'mae_ratios': ratios, 'accuracy_delta_pp': accuracy})
    result['scope'] = ('Full population reconstruction; verify matching split and mask '
                       'provenance in manifests. Not forecasting evidence.')
    write_json(args.output, result)
    print(json.dumps([{k: row[k] for k in ('reference', 'mae_groups',
                                         'mae_groups_improved')}
                      for row in result['comparisons']], indent=2))


if __name__ == '__main__':
    main()

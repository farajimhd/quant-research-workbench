"""Rebuild the bounded V20 comparison from immutable run receipts."""
import argparse
import json
from pathlib import Path
from ..v5.shards.phase1_contract import file_hash, write_json


KEYS = (
    'all_visible/raw_mae/1s/trade_close',
    'masked/raw_mae/1s/trade_close',
    'all_visible/candle/1s/trade/moving_direction_pct',
    'activity/all_visible/1s/trade_volume/moving_direction_pct',
    'activity/all_visible/daily/bid_count/raw_mae',
    'all_visible/range/daily/bid/bin_accuracy_pct',
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    reference = json.loads((root/'phase1-v18-joint-20261121/joint/latest-metrics.json').read_text())
    rows = []
    for run in ('phase1-v20-joint-singlehead-20261121', 'phase1-v20-visible-head-20261121'):
        manifest = json.loads((root/run/'manifest.json').read_text())
        for arm in manifest['arms']:
            directory = root/run/arm
            result = json.loads((directory/'result.json').read_text())
            if file_hash(directory/'checkpoint.pt') != result['checkpoint_sha']:
                raise ValueError(f'Changed checkpoint: {directory}')
            metrics = result['curve'][-1]['metrics']
            if result['curve'][-1]['step'] != manifest['steps']:
                raise ValueError(f'Incomplete evaluation: {directory}')
            ratios = sorted(((v/reference[k], k) for k,v in metrics.items()
                             if k.startswith('all_visible/raw_mae/') and reference.get(k,0)>0), reverse=True)
            drops = sorted(((v-reference[k], k) for k,v in metrics.items()
                            if k in reference and k.startswith(('all_visible/', 'activity/all_visible/'))
                            and k.endswith(('moving_direction_pct','bin_accuracy_pct'))))
            rows.append(dict(run=run, arm=arm, checkpoint_sha=result['checkpoint_sha'],
                seconds=result['seconds'], cached_windows_per_second=result['cached_windows_per_second'],
                wandb_url=result['wandb_url'], metrics={k:metrics[k] for k in KEYS},
                worst_raw_mae_ratios_vs_v18=ratios[:10], worst_accuracy_deltas_pp_vs_v18=drops[:10],
                decision='Development candidate only; no full-training qualification'))
    probes = json.loads((root/'phase1-v20-bar-probes-20261121/result.json').read_text())
    write_json(args.output, dict(experiments=rows, standalone_refitted_probes=probes,
        reference='Older V18 development checkpoint; not a matched initialization control',
        limitation='No full-population V20 validation or forecasting probe; do not promote automatically'))
    for row in rows:
        print(row['run'], row['arm'], row['metrics'])


if __name__ == '__main__':
    main()

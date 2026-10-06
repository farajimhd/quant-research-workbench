# Mechanical coverage and authenticated bounded TRAIN cache contracts.
import json
import torch
from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.action_contract import ActionAxes


def coverage_report(candidates, listings):
    """JSON-native mechanical counts; no target values or scores exported."""
    return dict(decisions=len(candidates),
        current_counts={name: int(sum(ActionAxes(listings, len(d.held_index)).action_class(d.token) == index
                                  for d in candidates)) for index, name in ((0, 'WAIT'), (1, 'ENTRY'), (5, 'HOLD'), (2, 'EXIT'))},
        future_counts=[{name: int(sum(d.forecast_actions is not None and len(d.forecast_actions) > h and d.forecast_actions[h] == index
                                 for d in candidates)) for index, name in enumerate(('ENTRY', 'WAIT', 'HOLD', 'EXIT'))} for h in range(5)])



def load_prepared(root, prior):
    """Only a hash-bound cache produced after full source verification."""
    receipt = json.loads((root/'prepared-train.json').read_text())
    expected = dict(dataset_sha256=prior['dataset_sha256'], market_dataset_sha256=prior['market_dataset_sha256'],
        bank_certificate_sha256=prior['bank_certificate_sha256'], market_certificate_sha256=prior['market_certificate_sha256'],
        context_split_receipt_sha256=prior['context_split_receipt_sha256'], input_listings=prior['input_listings'],
        day=prior['arguments']['day'], seconds=prior['arguments']['seconds'], target_listing_ids=prior['target_listing_ids'])
    if receipt.get('source_binding') != expected or receipt.get('version') != 'rl-v6-ranked-verified-train-cache-v1' or file_hash(root/'prepared-train.pt') != receipt['sha256']:
        raise ValueError('Prepared TRAIN source/content binding changed')
    saved = torch.load(root/'prepared-train.pt', map_location='cpu', weights_only=False)
    if (saved['scope']['day'] != prior['arguments']['day'] or saved['session'].role != 'train' or
            list(saved['session'].listings) != prior['input_listings'] or saved['scope']['target_ids'] != prior['target_listing_ids'] or
            saved['scope']['end_us']-saved['scope']['begin_us'] != prior['arguments']['seconds']*1_000_000 or
            coverage_report(saved['targets'], len(saved['session'].listings)) != saved['scope']['coverage']):
        raise ValueError('Prepared TRAIN role/population changed')
    return saved['session'], saved['targets'], saved['scope']


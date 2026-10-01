"""Independent-window ResNet classification control, not a trading policy.

Uses certified causal 120-candle windows and WAIT/HOLD identity labels. No
attention, action GRU, conditional regression, broker, or recurrent cache.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from research.rl_trading.v6.action_contract import ActionAxes, ACTION_NAMES
from research.rl_trading.v6.market_attention import VolumeRanker, MarketAttentionConfig
from research.rl_trading.v6.model import INPUT_WIDTH
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.teacher_data import load_wait_hold_teacher
from research.rl_trading.v6.training_gate import require_dataset


class ResidualBlock(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.layers = nn.Sequential(nn.Conv1d(width, width, 3, padding=1, bias=False),
            nn.GroupNorm(4, width), nn.ReLU(),
            nn.Conv1d(width, width, 3, padding=1, bias=False), nn.GroupNorm(4, width))

    def forward(self, x):
        return F.relu(x + self.layers(x))


class ResNetClassifier(nn.Module):
    """[K,120,F] independent ticker windows -> variable identity logits.

    GroupNorm avoids cross-observation batch statistics. Each observation's
    candidates are pooled separately; no information crosses decision rows.
    """
    def __init__(self, width=32):
        super().__init__()
        self.encoder = nn.Sequential(nn.Conv1d(INPUT_WIDTH, width, 7, stride=2,
            padding=3, bias=False), nn.GroupNorm(4, width), nn.ReLU(),
            ResidualBlock(width), nn.AvgPool1d(2), ResidualBlock(width),
            nn.AvgPool1d(2), ResidualBlock(width), nn.AdaptiveAvgPool1d(1))
        self.context = nn.Sequential(nn.Linear(width + 7, width), nn.ReLU())
        self.wait = nn.Linear(width, 1)
        self.enter = nn.Linear(width * 2, 1)
        self.held = nn.Sequential(nn.Linear(width * 2 + 11, width), nn.ReLU(),
                                  nn.Linear(width, 4))

    def forward(self, windows, records):
        embedding = self.encoder(windows.transpose(1, 2)).squeeze(-1)  # [K,D]
        result, start = [], 0
        for record in records:
            count = len(record['identities'])
            values = embedding[start:start + count]
            start += count
            context = self.context(torch.cat((values.mean(0), record['account'])))
            entry = self.enter(torch.cat((values, context.expand_as(values)), 1)).flatten()
            entry = entry.masked_fill(~record['enter'], torch.finfo(entry.dtype).min)
            held_values = values[record['held_slots']]
            held = self.held(torch.cat((held_values, context.expand_as(held_values),
                                       record['held_features']), 1))  # [H,4]
            held = held.masked_fill(~record['held_masks'], torch.finfo(held.dtype).min)
            result.append(torch.cat((self.wait(context), entry, held.T.flatten())))
        if start != len(embedding):
            raise ValueError('Packed window identity count differs')
        return result


def prepare(session, decisions, ranking):
    """Transient row-index windows; source tensors never include future rows."""
    features = [np.zeros((1, INPUT_WIDTH), np.float32)]
    clocks, row_ids, offset = [], [], 1
    for identity in session.listings:
        current = session.bank.listing(identity)
        previous = (session.previous.listing_tail(identity)
            if session.previous and identity in session.previous.manifest['offsets'] else None)
        values = []
        times = []
        for source in (previous, current):
            if source is not None:
                values.append(np.concatenate((source.scalar, source.levels.reshape(-1, 110)), 1))
                times.append(source.close_us)
        value, time = np.concatenate(values), np.concatenate(times)
        features.append(value)
        clocks.append(time)
        row_ids.append(np.arange(offset, offset + len(value), dtype=np.int64))
        offset += len(value)
    ranker = VolumeRanker(len(session.listings), ranking)
    groups = {}
    for item in decisions:
        groups.setdefault(item.close_us, []).append(item)
    records, windows = [], []
    for event in session.candle_events():
        ranker.observe(event.close_us, event.listing_index, session.bank.scalar[event.bank_row])
        ranker.select(event.close_us)
        for item in groups.pop(event.close_us, ()):
            selected = ranker.select(event.close_us, held=item.held_index.tolist())
            allowed = np.zeros(len(session.listings), bool)
            allowed[selected] = True
            enter = item.enter_allowed & allowed
            identities = np.union1d(np.flatnonzero(enter), item.held_index)
            # WAIT with no eligible/held listing needs a causal zero context.
            if not len(identities):
                identities = np.asarray([0])
            held_slots = np.searchsorted(identities, item.held_index)
            axes = ActionAxes(len(session.listings), len(item.held_index))
            kind = axes.action_class(item.token)
            if kind == 1:
                ticker = item.token - 1
                if not enter[ticker]:
                    raise ValueError('Baseline entry target outside causal ranking')
                token = 1 + int(np.searchsorted(identities, ticker))
            elif kind == 0:
                token = 0
            else:
                slot = (item.token - 1 - len(session.listings)) % len(item.held_index)
                token = 1 + len(identities) + (kind - 2) * len(item.held_index)
                if kind == 5:
                    token = 1 + len(identities) + 3 * len(item.held_index)
                token += slot
            block = np.zeros((len(identities), 120), np.int32)
            for k, identity in enumerate(identities):
                end = np.searchsorted(clocks[identity], item.close_us, side='right')
                rows = row_ids[identity][max(0, end - 120):end]
                if len(rows):
                    block[k, -len(rows):] = rows
                    if clocks[identity][end - 1] > item.close_us:
                        raise ValueError('Future candle in baseline window')
            records.append(dict(identities=identities, window_index=len(windows),
                account=item.account, enter=enter[identities], held_slots=held_slots,
                held_features=item.held_features,
                held_masks=np.stack((item.exit_allowed, item.stop_allowed,
                                     item.target_allowed, np.ones(len(item.held_index), bool)), 1),
                token=token, kind=kind, weight=item.sample_weight))
            windows.append(block)
    if groups or len(records) != len(decisions):
        raise ValueError('Baseline did not consume every audited label')
    return np.concatenate(features), windows, records


def run_epoch(model, prepared, optimizer, device, batch_size, mean, scale, seed):
    features, windows, records = prepared
    model.train(optimizer is not None)
    order = np.arange(len(records))
    if optimizer is not None:
        np.random.default_rng(seed).shuffle(order)
    confusion = np.zeros((6, 6), np.int64)
    exact = loss_sum = 0
    with torch.set_grad_enabled(optimizer is not None):
        for start in range(0, len(order), batch_size):
            items = [records[i] for i in order[start:start + batch_size]]
            indices = np.concatenate([windows[item['window_index']] for item in items])
            raw = features[indices]
            # Fit statistics on train only; zero padding stays exactly zero.
            raw = (raw - mean) / scale
            raw[indices == 0] = 0
            tensor = torch.from_numpy(raw).to(device)
            packed = [{**item, **{key:torch.as_tensor(item[key], device=device)
                for key in ('account','enter','held_slots','held_features','held_masks')}} for item in items]
            logits = model(tensor, packed)
            objectives = [F.cross_entropy(value[None], value.new_tensor([item['token']], dtype=torch.long))
                          for value, item in zip(logits, items)]
            loss = sum(value * item['weight'] for value,item in zip(objectives,items)) / sum(item['weight'] for item in items)
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite ResNet loss')
            if optimizer is not None:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
                optimizer.step()
            for value, objective, item in zip(logits, objectives, items):
                prediction = int(value.argmax())
                kind = ActionAxes(len(item['identities']), len(item['held_slots'])).action_class(prediction)
                confusion[item['kind'], kind] += 1
                exact += int(item['kind'] == 1 and prediction == item['token'])
                loss_sum += float(objective.detach())
    counts = confusion.sum(1)
    precision = np.divide(confusion.diagonal(), confusion.sum(0), out=np.zeros(6), where=confusion.sum(0)>0)
    recall = np.divide(confusion.diagonal(), counts, out=np.zeros(6), where=counts>0)
    return dict(decisions=len(records), mean_loss=loss_sum/len(records),
        counts=dict(zip(ACTION_NAMES, counts.tolist())),
        precision=dict(zip(ACTION_NAMES, precision.tolist())), recall=dict(zip(ACTION_NAMES, recall.tolist())),
        entry_token_accuracy=exact/int(counts[1]) if counts[1] else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, default=Path('D:/TradingML/runtimes'))
    parser.add_argument('--epochs', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--device', default='cuda')
    args = parser.parse_args()
    if not args.run_root.resolve().is_relative_to(args.runtime_root.resolve()) or args.run_root.exists():
        raise ValueError('Baseline requires a fresh runtime directory')
    if min(args.epochs, args.batch_size) < 1:
        raise ValueError('Positive baseline limits required')
    args.run_root.mkdir()
    try:
        dataset = require_dataset(args.dataset, runtime_root=args.runtime_root)
        random.seed(19); np.random.seed(19); torch.manual_seed(19); torch.set_num_threads(4)
        ranking = MarketAttentionConfig(**dataset['ranking'])
        prepared = {}
        for day in ('2026-08-05', '2026-08-24', '2026-08-25'):
            entry = next(d for d in dataset['days'] if d['day'] == day)
            session = open_session(Path(entry['bank_root']), runtime_root=args.runtime_root,
                                   previous_root=Path(entry['previous_root']))
            labels, _ = load_wait_hold_teacher(Path(entry['teacher_root']), session,
                runtime_root=args.runtime_root, audit_development=entry['role']=='development')
            prepared[day] = prepare(session, labels, ranking)
            print(json.dumps({'prepared':day, 'labels':len(labels)}), flush=True)
        train = prepared['2026-08-05'][0][1:]
        mean, scale = train.mean(0), np.maximum(train.std(0), 1e-3)
        model = ResNetClassifier().to(args.device)
        optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
        manifest = dict(version='v6-resnet-classification-control-v1', train_days=['2026-08-05'],
            development_days=['2026-08-24','2026-08-25'], sealed_test_accessed=False,
            ranking=asdict(ranking), epochs=args.epochs, batch_size=args.batch_size,
            scope='classification_only_no_recurrence_no_conditional_regressions',
            train_normalization='train_day_features_only', parameters=sum(p.numel() for p in model.parameters()))
        (args.run_root/'manifest.json').write_text(json.dumps(manifest, indent=2))
        for epoch in range(1, args.epochs+1):
            for day, data in prepared.items():
                metrics = run_epoch(model, data, optimizer if day=='2026-08-05' else None,
                    args.device, args.batch_size, mean, scale, epoch)
                row = dict(epoch=epoch, day=day, metrics=metrics)
                with (args.run_root/'metrics.jsonl').open('a') as stream:
                    stream.write(json.dumps(row)+'\n')
                print(json.dumps(row), flush=True)
            torch.save({'model':model.state_dict(), 'optimizer':optimizer.state_dict(),
                        'epoch':epoch, 'mean':mean, 'scale':scale}, args.run_root/f'epoch-{epoch:03}.pt')
        (args.run_root/'complete.json').write_text(json.dumps({'status':'classification_control_complete', **manifest}))
    except Exception as error:
        (args.run_root/'failure.json').write_text(json.dumps({'error':repr(error)}))
        raise


if __name__ == '__main__':
    main()

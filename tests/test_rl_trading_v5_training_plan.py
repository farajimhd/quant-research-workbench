from pathlib import Path

import pytest

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.publish_dynamic_split import TRAIN, DEVELOPMENT, TEST
from research.rl_trading.v1.v5_training_plan import load_training_plan
from src.market_engine.level_book_store import write


def test_training_plan_requires_audit_and_does_not_open_sealed_rows(tmp_path: Path):
    inventory = {}
    features = {}
    execution = {}
    for split, days in (('train', TRAIN), ('development', DEVELOPMENT),
                        ('test_sealed', TEST)):
        items = []
        for day in days:
            root = tmp_path / split / str(day)
            root.mkdir(parents=True)
            write(root / 'complete.json', {'plan_hash': str(day)})
            items.append({'date': str(day), 'root': str(root),
                          'complete_hash': file_hash(root / 'complete.json')})
            if split != 'test_sealed':
                feature = tmp_path / 'features' / str(day)
                feature.mkdir(parents=True)
                write(feature / 'complete.json', {'date': str(day)})
                features[str(day)] = str(feature)
            if split == 'development':
                grid = tmp_path / 'execution' / str(day)
                grid.mkdir(parents=True)
                write(grid / 'complete.json', {'date': str(day)})
                execution[str(day)] = str(grid)
        inventory[split] = items
    split = {'version': 'rl-dynamic-forward-split-v1', **inventory}
    split['plan_hash'] = digest(split)
    split_path = tmp_path / 'manifest.json'
    write(split_path, split)
    audit = {'version': 'rl-dynamic-teacher-comparison-v1',
             'split_manifest_hash': file_hash(split_path),
             'days': [{'date': str(day)} for day in TRAIN + DEVELOPMENT]}
    audit['report_hash'] = digest(audit)
    audit_path = tmp_path / 'comparison.json'
    write(audit_path, audit)
    plan = load_training_plan(split_path, audit_path, features, execution,
                              tmp_path)
    assert (len(plan.train), len(plan.development), len(plan.test_sealed)) == (17, 2, 1)
    assert plan.test_sealed[0].feature_root is None
    assert plan.test_sealed[0].execution_root is None
    with pytest.raises(ValueError, match='Development replay grid required'):
        load_training_plan(split_path, audit_path, features, {}, tmp_path)

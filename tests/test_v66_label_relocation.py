"""Relocation preserves sealed bytes and recovers after the directory rename."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from research.market_labels.v2 import relocate as migration
from research.market_labels.v2.contract import VERSION
from research.market_shards.v1.contract import sha,write_json


class Relocation(unittest.TestCase):
    def fixture(self,root):
        market=root/'market_shards/datasets/test';market.mkdir(parents=True)
        for name in ('training.json','validation.json','source.json','complete.json'):write_json(market/name,{'name':name})
        source=root/'market_shards/labels/v66-1a1b-spread-v2';day=source/'2026-01-05';day.mkdir(parents=True)
        (source/'writer.lock').write_bytes(b'0');(day/'labels.parquet').write_bytes(b'sealed example data')
        binding=dict(source_manifests={r:sha(market/(r+'.json')) for r in ('training','validation')})
        write_json(day/'complete.json',dict(binding=binding,files={'labels.parquet':dict(sha256=sha(day/'labels.parquet'),rows=1)}))
        write_json(source/'dataset.json',dict(version=VERSION,full_inventory=True,status='verified_review_only',source=str(market),binding=binding,
            days=[dict(day='2026-01-05',certificate=sha(day/'complete.json'))]))
        registry=dict(version=VERSION,dataset=str(source/'dataset.json'),sha256=sha(source/'dataset.json'))
        write_json(root/'v66-active-labels.json',registry)
        write_json(root/('v66-label-registry-'+VERSION+'.json'),registry)
        return source,market,market/'labels/1a1b-spread-v2'

    def test_move_preserves_hashes_updates_pointers_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            root=Path(folder);source,market,target=self.fixture(root)
            before=migration.inventory(source)
            result=migration.relocate(root,source,market)
            self.assertEqual(result['status'],'complete');self.assertFalse(source.exists())
            self.assertEqual(migration.inventory(target),before)
            self.assertEqual(json.loads((root/'v66-active-labels.json').read_bytes())['dataset'],str(target/'dataset.json'))
            self.assertEqual(migration.relocate(root,source,market)['sha256'],result['sha256'])
            (target/'2026-01-05/labels.parquet').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'integrity'):migration.relocate(root,source,market)

    def test_resume_after_rename_before_registry_update(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            root=Path(folder);source,market,target=self.fixture(root)
            original=migration.inventory
            def interruption(path):
                if path==target:raise RuntimeError('Simulated interruption after rename')
                return original(path)
            with patch.object(migration,'inventory',side_effect=interruption):
                with self.assertRaises(RuntimeError):migration.relocate(root,source,market)
            self.assertFalse(source.exists());self.assertTrue(target.exists())
            self.assertEqual(migration.relocate(root,source,market)['status'],'complete')

    def test_refuses_existing_destination_and_runtime_escape(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            root=Path(folder);source,market,target=self.fixture(root);target.mkdir(parents=True)
            with self.assertRaises(ValueError):migration.relocate(root,source,market)
            with self.assertRaisesRegex(ValueError,'runtime'):migration.relocate(root,root.parent/'other-labels',market)


if __name__=='__main__':unittest.main()

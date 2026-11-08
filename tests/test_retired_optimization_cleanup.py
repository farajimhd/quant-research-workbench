"""Deletion scope, retained inputs, changed-file protection and restart behavior."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.cleanup_retired_optimization import Cleanup


class RetiredCleanup(unittest.TestCase):
    def setup_job(self,root):
        base=root/'vectorized_backtest';target=base/'torch_backtest_v59';target.mkdir(parents=True)
        (target/'receipt.json').write_text('old output')
        (target/'inputs').mkdir();(target/'inputs/bars.bin').write_bytes(b'input bars')
        (target/'candidate-inputs.npz').write_bytes(b'preserved input snapshot')
        (target/'market.parquet').write_bytes(b'preserved market input')
        current=base/'torch_backtest_v66';current.mkdir();(current/'receipt.json').write_text('retained newer run')
        proposal=root/'proposal.json'
        value=dict(first_pass=dict(roots=[dict(path=str(target))]),excluded_directory_names=['inputs'],excluded_directory_prefixes=['feature-cache'],
            excluded_file_prefixes=['feature-bank'],preserved_file_extensions=['.npz','.npy','.arrow'])
        proposal.write_text(json.dumps(value));digest=hashlib.sha256(proposal.read_bytes()).hexdigest()
        job=Cleanup(proposal,root/'cleanup',base,digest)
        return job,target,current

    def test_only_approved_outputs_deleted_and_restart_is_safe(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            job,target,current=self.setup_job(Path(folder))
            try:
                with patch.object(job,'active_users'):
                    job.plan();self.assertEqual(job.counts['pending'],1);job.execute();job.execute()
                self.assertFalse((target/'receipt.json').exists())
                self.assertEqual((target/'inputs/bars.bin').read_bytes(),b'input bars')
                self.assertTrue((target/'candidate-inputs.npz').exists());self.assertTrue((target/'market.parquet').exists())
                self.assertTrue((current/'receipt.json').exists());self.assertEqual(job.counts['deleted'],1)
            finally:job.db.close()

    def test_changed_file_is_retained(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            job,target,_=self.setup_job(Path(folder))
            try:
                with patch.object(job,'active_users'):
                    job.plan();(target/'receipt.json').write_text('changed after plan');job.execute()
                self.assertTrue((target/'receipt.json').exists());self.assertEqual(job.counts['changed'],1)
            finally:job.db.close()

    def test_live_process_blocks_plan_and_new_versions_are_rejected(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as folder:
            job,_,current=self.setup_job(Path(folder))
            try:
                with patch.object(job,'active_users',side_effect=RuntimeError('active run')):
                    with self.assertRaises(RuntimeError):job.plan()
                with self.assertRaises(ValueError):job.guard(current/'receipt.json')
                with self.assertRaises(ValueError):job.guard(job.base/'torch_backtest_v59/inputs/bars.bin')
                with self.assertRaises(ValueError):job.guard(job.base/'../outside.txt')
            finally:job.db.close()


if __name__=='__main__':unittest.main()

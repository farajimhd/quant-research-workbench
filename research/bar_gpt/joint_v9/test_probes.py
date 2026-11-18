import tempfile,unittest
from pathlib import Path
import numpy as np
from .test_model import examples
from .data import cache
from .probes import labels,summaries,fit_evaluate,causal_controls
from .launch import controller_lock
from .data import calibrate
from .model import Config,JointModel
from .qualify import phase1_transfer
from ..v5.shards.phase1_contract import digest,file_hash,write_json


class ProbeContract(unittest.TestCase):
    def test_single_label_validity_groups_keep_output_dimension(self):
        x=np.arange(16,dtype=np.float32).reshape(8,2);y=np.stack((x[:,0],x[:,1]),1)
        mask=np.ones((8,2),bool);mask[0,1]=False
        result=fit_evaluate({'train':x,'development':x},{k:(['a','b'],y,mask) for k in ('train','development')})
        self.assertEqual(result['a']['development']['count'],8);self.assertEqual(result['b']['development']['count'],7)

    def test_six_frame_transfer_reads_only_sealed_payload(self):
        d=cache(examples());model=JointModel(Config(width=32,embedding=64,layers=1,global_layers=1,dropout=0,precision='fp32'),**calibrate(d)).eval()
        rows=examples()['context'].reshape(-1).copy();rows['timeframe_us'][::2]=100000
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes/bar_gpt/joint-v9-20261117') as directory:
            root=Path(directory);np.save(root/'audit.npy',rows,allow_pickle=False)
            manifest=dict(files={'audit.npy':file_hash(root/'audit.npy')});manifest['hash']=digest(manifest);write_json(root/'manifest.json',manifest)
            report=phase1_transfer(model,root,'cpu')
            self.assertEqual(report['full']['frames']['100000']['windows'],32)
            self.assertEqual(report['masked16']['frames']['100000']['nonfinite'],0)
            with (root/'audit.npy').open('ab') as f:f.write(b'changed')
            with self.assertRaises(ValueError):phase1_transfer(model,root,'cpu')

    def test_labels_do_not_enter_summary_features(self):
        rows=examples();first=cache(rows);features=summaries(first);names,y,v=labels(first)
        rows['future']['values']*=2
        second=cache(rows)
        np.testing.assert_array_equal(features,summaries(second))
        np.testing.assert_array_equal(causal_controls(first),causal_controls(second))
        self.assertFalse(np.array_equal(y,labels(second)[1]))
        self.assertEqual(len(names),23);self.assertEqual(v.shape,y.shape)
        first['target_valid'][0,0,4]=False
        self.assertFalse(labels(first)[2][0,3])

    def test_controller_admission_is_exclusive(self):
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes/bar_gpt/joint-v9-20261117') as directory:
            root=Path(directory)
            with controller_lock(root):
                with self.assertRaises(FileExistsError):
                    with controller_lock(root):pass
            with controller_lock(root):pass
            self.assertFalse((root/'controller.lock').exists())


if __name__=='__main__':unittest.main()

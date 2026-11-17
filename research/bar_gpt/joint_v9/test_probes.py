import tempfile,unittest
from pathlib import Path
import numpy as np
from .test_model import examples
from .data import cache
from .probes import labels,summaries
from .launch import controller_lock


class ProbeContract(unittest.TestCase):
    def test_labels_do_not_enter_summary_features(self):
        rows=examples();first=cache(rows);features=summaries(first);names,y,v=labels(first)
        rows['future']['values']*=2
        second=cache(rows)
        np.testing.assert_array_equal(features,summaries(second))
        self.assertFalse(np.array_equal(y,labels(second)[1]))
        self.assertEqual(len(names),11);self.assertEqual(v.shape,y.shape)
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

import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from research.vectorized_backtest.v59.observer_io import ObserverIO,InputLane


class ObserverLanes(unittest.TestCase):
    def test_read_and_keys_do_not_wait_for_slow_file(self):
        import threading
        started=threading.Event();release=threading.Event()
        def slow(path):
            started.set();release.wait(2);return {'ready':True}
        root=Path(r'D:\TradingML\runtimes\vectorized_backtest\torch_backtest_v59\tests')
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            path=Path(folder)/'status.json';path.write_text('{}')
            with patch('research.vectorized_backtest.v59.observer_io.read_json',slow):
                io=ObserverIO();keys=InputLane(lambda:True,lambda:'j')
                try:
                    io.read(path);self.assertTrue(started.wait(1))
                    begin=time.perf_counter()
                    for _ in range(100):self.assertEqual(io.read(path),{})
                    self.assertLess(time.perf_counter()-begin,.1)
                    self.assertEqual(keys.events.get(timeout=.1),'j')
                    release.set()
                    deadline=time.monotonic()+2
                    while io.read(path)!={'ready':True} and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(io.read(path),{'ready':True})
                finally:release.set();keys.close();io.close()


if __name__=='__main__':unittest.main()

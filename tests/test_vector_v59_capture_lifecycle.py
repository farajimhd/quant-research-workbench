import gc
import unittest
from concurrent.futures import Future
from research.vectorized_backtest.v59.capture_lifecycle import defer_cyclic_collection
from research.vectorized_backtest.v59.session_pipeline import SessionProgress,independent_sessions


class CaptureLifecycleTests(unittest.TestCase):
    def test_collection_deferred_and_restored_on_failure(self):
        prior=gc.isenabled()
        try:
            gc.enable()
            with self.assertRaisesRegex(RuntimeError,'capture'):
                with defer_cyclic_collection():
                    self.assertFalse(gc.isenabled())
                    with defer_cyclic_collection():self.assertFalse(gc.isenabled())
                    self.assertFalse(gc.isenabled())
                    raise RuntimeError('capture')
            self.assertTrue(gc.isenabled())
            gc.disable()
            with defer_cyclic_collection():self.assertFalse(gc.isenabled())
            self.assertFalse(gc.isenabled())
        finally:
            if prior:gc.enable()
            else:gc.disable()

    def test_failed_future_updates_status_and_stops_dispatch(self):
        class Panel:
            def publish(self,**values):self.values=values
        panel=Panel();progress=SessionProgress(panel,['a','b'],6144,0,'training')
        submitted=[]
        def submit(*args):
            submitted.append(args[2]);f=Future();f.set_exception(RuntimeError('capture failed'));return f
        with self.assertRaisesRegex(RuntimeError,'capture failed'):
            independent_sessions(['a','b'],1,submit,progress)
        self.assertEqual(submitted,['a'])
        self.assertEqual(progress.rows['a']['status'],'failed')
        self.assertEqual(panel.values['active_sessions'],[])
        self.assertEqual(progress.rows['b']['status'],'queued')

"""Focused format, restart, sampling and live SELECT-only SQL checks.

Run with python -B -m unittest research.bar_gpt.v5.shards.test_shards.
Set BARGPT_LIVE_TESTS=1 to exercise synthetic events inside ClickHouse SELECTs;
these tests do not create tables or alter canonical data.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import io
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np
from rich.console import Console

from . import sql
from .contract import SAMPLE_DTYPE, digest, pack, write_json, file_hash
from .recovery import resolve_binding
from .progress import render
from .source import arrow, client, preflight
from .storage import BAR_DTYPE, Budget, publish, unpack_bars, verify_receipt, adjust_splits


class FormatTests(unittest.TestCase):
    def test_backward_split_adjustment_counts_and_future_actions(self):
        """Both split directions change historical price/size units, never event counts."""
        samples = np.zeros(2, dtype=SAMPLE_DTYPE)
        before, after = sql.clock("2020-08-28", 16), sql.clock("2020-08-31", 16)
        samples["end_us"][0] = before + np.arange(64)
        samples["end_us"][1] = np.r_[before + np.arange(32), after + np.arange(32)]
        samples["values"] = 100.
        samples["valid"] = 1
        action = dict(execution_date="2020-08-31", split_from=1., split_to=4.)
        adjust_splits(samples, [action])
        self.assertTrue((samples["values"][0] == 100.).all())  # No future action leakage.
        self.assertTrue((samples["values"][1, :32, 3] == 25.).all())
        self.assertTrue((samples["values"][1, :32, 4] == 400.).all())
        self.assertTrue((samples["values"][1, :, [5, 11, 17]] == 100.).all())
        self.assertTrue((samples["values"][1, 32:] == 100.).all())
        adjust_splits(samples, [dict(action, split_from=4., split_to=1.)])
        self.assertTrue((samples["values"] == 100.).all())  # Inverse action restores units.

    def test_real_windows_reader_lock_retries_atomic_publication(self):
        runtime = Path("D:/TradingML/runtimes/bar_gpt/tests")
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as temporary:
            path = Path(temporary) / "progress.json"
            write_json(path, {"version": 1})
            # FILE_SHARE_READ deliberately omits DELETE, matching the sharing
            # conflict raised by a reader during os.replace on Windows/SMB.
            program = '''import ctypes,sys,time
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
k.CreateFileW.restype=wintypes.HANDLE
k.CloseHandle.argtypes=[wintypes.HANDLE]
h=k.CreateFileW(sys.argv[1],0x80000000,1,None,3,0,None)
if h==wintypes.HANDLE(-1).value: raise ctypes.WinError(ctypes.get_last_error())
print('locked',flush=True)
time.sleep(.4)
k.CloseHandle(h)
'''
            child = subprocess.Popen([sys.executable, "-B", "-u", "-c", program, str(path)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                began = time.monotonic()
                write_json(path, {"version": 2})
                self.assertGreater(time.monotonic()-began, .25)
                self.assertEqual(path.read_text().count('"version": 2'), 1)
                self.assertEqual(child.wait(timeout=5), 0)
            finally:
                if child.poll() is None:
                    child.terminate(); child.wait(timeout=5)
                child.stdout.close(); child.stderr.close()

    def test_recovery_preserves_binding_and_rejects_data_changes(self):
        import json
        runtime = Path("D:/TradingML/runtimes/bar_gpt/tests")
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as temporary:
            root = Path(temporary)
            baseline = root / "research/bar_gpt/v5/shards/contract.py"
            baseline.parent.mkdir(parents=True)
            current_file = Path(__file__).with_name("contract.py")
            baseline.write_bytes(current_file.read_bytes())
            old = dict(width=64, code={"contract.py": file_hash(baseline), "source.py": "unchanged"})
            current = dict(width=64, code={**old["code"], "build.py": "reviewed-controller-fix"})
            saved = dict(contract=old, binding=digest(old))
            receipt = root / "resume.json"
            write_json(receipt, dict(old_binding=saved["binding"], new_contract_hash=digest(current),
                                    new_code=current["code"], baseline_root=str(root)))
            self.assertEqual(resolve_binding(saved, current, receipt), saved["binding"])
            changed = dict(current, width=128)
            with self.assertRaises(ValueError):
                resolve_binding(saved, changed, receipt)
            with self.assertRaises(ValueError):
                resolve_binding(saved, current, None)

    def test_atomic_mmap_and_receipt_reuse(self):
        runtime = Path("D:/TradingML/runtimes/bar_gpt/tests")
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as temporary:
            samples = pack(np.ones((3, 64, 21)), np.ones((3, 64, 21), dtype=np.uint8),
                           np.tile(np.arange(1, 65), (3, 1)), 100000, 0)
            budget = Budget(10**7)
            metadata = dict(identity="test")
            result = publish(temporary, samples, np.empty(0, dtype=BAR_DTYPE), metadata, budget, 16384)
            self.assertEqual(result["samples"], 3)
            self.assertEqual(len(result["files"]), 3)
            self.assertFalse((Path(temporary) / "coarse.npy").exists())
            first = np.load(Path(temporary)/"samples-0000.npy", mmap_mode="r", allow_pickle=False)
            np.testing.assert_array_equal(first["values"][0], samples["values"][0])
            first._mmap.close()  # Release the Windows file handle before cleanup.
            used = budget.used
            self.assertEqual(publish(temporary, samples, np.empty(0, dtype=BAR_DTYPE),
                                     metadata, budget, 16384), result)
            self.assertEqual(budget.used, used)
            with self.assertRaises(ValueError):
                verify_receipt(temporary, "different")

    def test_budget_is_atomic(self):
        budget = Budget(100)
        with ThreadPoolExecutor(8) as pool:
            admitted = list(pool.map(budget.reserve, [30]*20))
        self.assertEqual(sum(admitted), 3)
        self.assertEqual(budget.reserved, 90)

    def test_pack_rejects_nonmonotonic_clocks(self):
        with self.assertRaises(ValueError):
            pack(np.zeros((1,64,21)), np.ones((1,64,21), dtype=np.uint8),
                 np.zeros((1,64), dtype=np.int64), 100000, 0)

    def test_dst_is_explicit(self):
        self.assertEqual(sql.clock("2024-03-11",4)-sql.clock("2024-03-08",4),
                         (3*24-1)*3600*1000000)

    def test_panel_normal_and_compact_plain_output(self):
        for width in (70, 110):
            stream = io.StringIO()
            console = Console(file=stream, width=width, force_terminal=False)
            console.print(render(dict(state="running", updated=0, bytes=500, target_bytes=1000,
                workers=[dict(id=i, ticker="LONG.TICKER",day="2025-10-10",phase="query",done=20,total=250)
                         for i in range(1,7)], failed=[]), width))
            text = stream.getvalue()
            self.assertIn("stale", text)
            self.assertNotIn("\x1b", text)
            self.assertLessEqual(max(map(len, text.splitlines())), width)


@unittest.skipUnless(os.environ.get("BARGPT_LIVE_TESTS") == "1", "Live SELECT tests opt in")
class SqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = client()
        cls.rules = preflight(cls.client)["rules"]

    @classmethod
    def tearDownClass(cls):
        if getattr(cls.client, "_bar_arrow_connection", None):
            cls.client._bar_arrow_connection.close()
        cls.client.close()

    def fixture(self, events, initial=2, fill=True, limit=True, offset=0):
        """Events: (offset_us, kind, ask/trade cents, bid cents, condition IDs)."""
        sampling_offset = offset  # Keep the sampling seed independent of event timestamps below.
        opened = sql.clock("2019-01-03", 4)
        if fill:
            # Complete an observed 64-bar block without altering the first two seconds.
            events = list(events) + [(i*1_000_000, 0, 1200, 1100, []) for i in range(2, 66)]
        columns = ("ticker String,ordinal UInt64,event_meta UInt8,sip_timestamp_us UInt64,"
                   "price_primary_int UInt32,price_secondary_int UInt32,size_primary Float32,"
                   "size_secondary Float32,condition_token_1 UInt8,condition_token_2 UInt8,"
                   "condition_token_3 UInt8,condition_token_4 UInt8,condition_token_5 UInt8")
        data = []
        for ordinal, (offset, kind, price, bid, tokens) in enumerate(events):
            kind = 65 if kind == 1 else kind  # Normal fixture trades carry ARTE's verified-timely bit.
            token_values = list(tokens) + [0]*(5-len(tokens))
            data.append("("+",".join(map(str, ["'TEST'",ordinal,kind,opened+offset,
                                               price,bid,10,20,*token_values]))+")")
        raw = f"SELECT * FROM values('{columns}',"+",".join(data)+")"
        with patch.object(sql, "source", return_value=raw), patch.object(sql,"seed_for",return_value=sampling_offset):
            statement = sql.day_query("TEST","2019-01-03",[],self.rules,initial,0,100)
        if limit:
            statement = statement.replace("ORDER BY g.frame,g.bucket FORMAT ArrowStream",
                "WHERE g.frame=1000000 AND g.bucket<3 ORDER BY g.frame,g.bucket FORMAT ArrowStream")
        return arrow(self.client, statement, "bargpt-v5-test-"+digest(events)[:16])

    def test_successive_observed_bars_preserve_clock_gaps(self):
        """Sparse canonical events must yield 64 consecutive observations, not 6.4 empty seconds."""
        events = [(i*10_000_000, 0, 1200, 1100, []) for i in range(64)]
        result = self.fixture(events, fill=False, limit=False)
        samples, coarse = unpack_bars(result, 0)
        self.assertEqual(len(samples), 4)  # Fine minute tail is now right-padded, not discarded.
        self.assertEqual(len(coarse), 1)  # Premarket-only observations do not create a regular-session daily bar.
        full = samples[samples["timeframe_us"] < 60_000_000]
        self.assertTrue(np.all(np.diff(full["end_us"], axis=1) == 10_000_000))
        self.assertTrue(full["valid"][..., [9, 15]].all())
        for frame in (100000, 1000000, 10000000):
            rows = result.filter(result["g.frame"] == frame)
            self.assertEqual(rows["observed_index"].to_list(), list(range(64)))

    def test_observed_windows_do_not_overlap(self):
        events = [(i*10_000_000, 0, 1200, 1100, []) for i in range(128)]
        result = self.fixture(events, fill=False, limit=False)
        samples, _ = unpack_bars(result, 0)
        for frame in (100000, 1000000, 10000000):
            selected = samples[samples["timeframe_us"] == frame]
            self.assertEqual(len(selected), 2)
            self.assertEqual(len(np.unique(selected["end_us"])), 128)
            self.assertLess(selected["end_us"][0, -1], selected["end_us"][1, 0])

    def test_no_eligible_events_only_returns_metadata(self):
        result = self.fixture([(0, 3, 0, 0, [])], fill=False, limit=False)
        samples, coarse = unpack_bars(result, 0)
        self.assertEqual(len(samples), 0)
        self.assertEqual(len(coarse), 0)
        self.assertEqual(result["g.frame"].to_list(), [0])

    def token(self, family, modifier):
        return next(int(r["token_id"]) for r in self.rules
                    if r["source_family"] == family and int(r["modifier_int"]) == modifier)

    def test_adjustment_matches_arte_execution_date_formula(self):
        """Independent existing ARTE SQL verifies compounded forward/reverse factors."""
        from pipelines.market_sip.events.market_day_sql import split_factor
        from .source import rows

        actions = [dict(provider_ticker="TEST", execution_date="2020-08-31", split_from=1., split_to=4.),
                   dict(provider_ticker="TEST", execution_date="2020-09-01", split_from=10., split_to=1.)]
        expression = split_factor(actions, "2020-09-02", "TEST", "toDate('2020-08-28')")
        expected = rows(self.client, f"SELECT {expression} AS factor")[0]["factor"]
        sample = np.zeros(1, dtype=SAMPLE_DTYPE)
        sample["end_us"][0, :32] = sql.clock("2020-08-28", 16) + np.arange(32)
        sample["end_us"][0, 32:] = sql.clock("2020-09-02", 16) + np.arange(32)
        sample["values"] = 100.
        adjust_splits(sample, actions)
        self.assertEqual(float(sample["values"][0, 0, 3]), 100*expected)
        self.assertEqual(float(sample["values"][0, 0, 4]), 100/expected)

    def test_boundary_transitions_and_repeated_messages(self):
        normal = self.token("cta_security_status",100)
        halt = self.token("cta_security_status",102)
        resume = self.token("cta_security_status",116)
        f = self.fixture([(-1,0,0,0,[normal]), (0,0,0,0,[halt]),
            (100000,0,1200,1100,[]), (200000,0,0,0,[resume]),
            (300000,0,0,0,[halt]), (400000,0,0,0,[halt]),
            (500000,1,1150,0,[]), (1000000,0,0,0,[resume])])
        values = f["values"].to_list(); valid = f["valid"].to_list()
        self.assertEqual(values[0][-3:], [0,1,2])
        self.assertEqual(values[1][-3:], [1,0,0])
        self.assertEqual(valid[0][-3:], [1,1,1])
        self.assertAlmostEqual(values[0][0],11.5)
        self.assertEqual(values[0][6:10], [11,11,11,0])
        self.assertEqual(values[0][12:16], [12,12,12,0])
        self.assertEqual(valid[0][9], 0)  # Later zero/invalid quote clears the earlier valid close.
        self.assertEqual(values[0][5],1)
        self.assertEqual(values[0][11],1)
        self.assertEqual(values[0][17],1)

    def test_quote_carry_clears_on_invalidation_and_never_looks_ahead(self):
        events = [(i*1_000_000, 65, 1150, 0, []) for i in range(64)]
        events += [(0, 0, 1200, 1100, []), (20_500_000, 0, 0, 0, []),
                   (40_000_000, 0, 1400, 1300, [])]
        result = self.fixture(events, fill=False, limit=False)
        seconds = result.filter(result["g.frame"] == 1_000_000)
        values = np.array(seconds["values"].to_list())
        masks = np.array(seconds["valid"].to_list())
        self.assertEqual(len(seconds), 64)
        np.testing.assert_array_equal(values[1:20, 9], np.full(19, 11.))
        self.assertTrue((values[1:20, 11] == 0).all())  # Carry is not a new quote event.
        self.assertTrue((masks[20:40, 9] == 0).all())
        self.assertTrue((values[40:, 9] == 13).all())

    def test_quote_carry_is_seeded_before_sample_start(self):
        events = [(i*1_000_000, 65, 1150, 0, []) for i in range(128)]
        events += [(0, 0, 1200, 1100, [])]
        result = self.fixture(events, fill=False, limit=False, offset=32)
        seconds = result.filter(result["g.frame"] == 1_000_000)
        values = np.array(seconds["values"].to_list())
        self.assertEqual(len(seconds), 96)  # 64 full bars plus the retained 32-bar session tail.
        self.assertTrue((values[:, 9] == 11).all())
        self.assertTrue((values[:, 11] == 0).all())

    def test_unknown_delayed_and_invalid_trades_do_not_update_prices(self):
        events = [(i*1_000_000, 0, 1200, 1100, []) for i in range(64)]
        # 193 = evaluated+delayed; 3 = unknown reporting with high-precision price encoding.
        events += [(10, 65, 1150, 0, []), (20, 193, 9900, 0, []),
                   (30, 3, 990000, 0, []), (40, 65, 0, 0, [])]
        result = self.fixture(events, fill=False, limit=False)
        row = result.filter(result["g.frame"] == 1_000_000)["values"][0].to_list()
        self.assertEqual(row[:6], [11.5, 11.5, 11.5, 11.5, 10., 1.])

    def test_unknown_is_not_normal_and_conflicts_invalidate(self):
        halt = self.token("cta_security_status",102)
        resume = self.token("cta_security_status",103)
        f = self.fixture([(0,0,0,0,[halt]), (1500000,0,0,0,[halt,resume])])
        self.assertEqual(f["valid"][0].to_list()[-3:], [0,1,0])
        self.assertEqual(f["valid"][1].to_list()[-3:], [1,0,0])
        self.assertEqual(f["values"][0].to_list()[-1], 0)

    def test_session_tail_padding_keeps_real_bars_and_clears_padding(self):
        """65 observed seconds yield one full context and one padded tail per fine scale."""
        events = [(i*1_000_000, 0, 1200, 1100, []) for i in range(65)]
        result = self.fixture(events, fill=False, limit=False)
        samples, _ = unpack_bars(result, 0)
        seconds = samples[samples["timeframe_us"] == 1_000_000]
        self.assertEqual((seconds["end_us"] > 0).sum(axis=1).tolist(), [64, 1])
        self.assertFalse(seconds["valid"][1, 1:].any())
        self.assertFalse(seconds["values"][1, 1:].any())


if __name__ == "__main__":
    unittest.main()

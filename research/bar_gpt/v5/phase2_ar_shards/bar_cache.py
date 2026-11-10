"""Source-bound, unadjusted session contexts; bounded RAM with persistent misses."""

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import psutil

from .coarse_cache import owned_lock
from .contract import BAR, FRAMES
from ..shards.contract import digest, file_hash, write_json
from ..shards.sql import clock
from ..quote_repair_v1 import verify_seal


class BarCache:
    """Persist all coarse bars and last64 fine bars per complete source session.

    Fine tails are ONLY usable for history, never a substitute for full current
    session output. Each session retains enough observed rows to reconstruct
    exactly the final64 history rows across any number of sparse sessions.
    """

    def __init__(self, root, binding, memory_bytes=64 * 1024**2, fine_tail_rows=64):
        self.root, self.binding = Path(root), binding
        if fine_tail_rows < 64:
            raise ValueError("Context tails must retain at least64 bars")
        self.fine_tail_rows = fine_tail_rows
        self.memory_bytes = memory_bytes
        self.memory = OrderedDict()
        self.used = 0
        self.ticker = None
        self.indices = {}

    def identity(self, ticker, row, coarse):
        return dict(version="unadjusted-session-context-v1", binding=self.binding,
                    ticker=ticker, source_row=row, coarse=bool(coarse),
                    fine_tail_rows=self.fine_tail_rows, price_basis="unadjusted")

    def _paths(self, identity):
        key = digest(identity)
        directory = self.root / digest(self.binding)[:24] / digest(identity["ticker"])[:24] / identity["source_row"]["source_date"][:7]
        return key, directory / ("coarse.json" if identity["coarse"] else "fine.json"), directory

    def _remember(self, key, arrays):
        size = sum(a.nbytes for a in arrays)
        if size > self.memory_bytes:
            return
        prior = self.memory.pop(key, None)
        if prior is not None:
            self.used -= sum(a.nbytes for a in prior)
        self.memory[key] = arrays
        for array in arrays:
            array.flags.writeable = False
        self.used += size
        while self.used > self.memory_bytes:
            _, old = self.memory.popitem(last=False)
            self.used -= sum(a.nbytes for a in old)

    def _select_ticker(self, ticker):
        if ticker != self.ticker:
            self.memory.clear()
            self.indices.clear()
            self.used = 0
            self.ticker = ticker

    def _index(self, marker):
        if not marker.exists():
            return dict(entries={})
        stat = marker.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self.indices.get(marker)
        if cached and cached[0] == stamp:
            return cached[1]
        index = json.loads(marker.read_text())
        verify_seal(index, "hash")
        self.indices[marker] = (stamp, index)
        return index

    def _load(self, descriptor, directory):
        if Path(descriptor["name"]).name != descriptor["name"]:
            raise ValueError("Unsafe persistent context path")
        payload = directory / descriptor["name"]
        if payload.stat().st_size != descriptor["bytes"] or file_hash(payload) != descriptor["sha256"]:
            raise ValueError("Persistent session context changed")
        with np.load(payload, allow_pickle=False) as archive:
            packed = [archive[f"f{i}"].copy() for i in range(5)]
        receipt_path = payload.with_suffix(".json")
        receipt = json.loads(receipt_path.read_text())
        verify_seal(receipt,"hash")
        if receipt["payload"] != descriptor:
            raise ValueError("Persistent context descriptor changed")
        for key, item in receipt["entries"].items():
            if digest(item["identity"]) != key or item["identity"]["binding"] != self.binding:
                raise ValueError("Persistent context source identity changed")
            arrays = []
            for i, (lo, hi) in enumerate(item["slices"]):
                if not 0 <= lo <= hi <= len(packed[i]) or packed[i].dtype != BAR:
                    raise ValueError("Persistent context bounds changed")
                a = packed[i][lo:hi]
                if np.any(np.diff(a["end_us"]) <= 0) or (not item["identity"]["coarse"] and len(a)>self.fine_tail_rows):
                    raise ValueError("Persistent context geometry changed")
                arrays.append(a)
            self._remember(key, arrays)
        return receipt

    def get(self, ticker, row, coarse):
        identity = self.identity(ticker, row, coarse)
        key, marker, directory = self._paths(identity)
        self._select_ticker(ticker)
        if key in self.memory:
            self.memory.move_to_end(key)
            return self.memory[key], "memory"
        index = self._index(marker)
        if key not in index["entries"]:
            return None, None
        receipt = self._load(index["entries"][key], directory)
        if key not in receipt["entries"] or receipt["entries"][key]["identity"] != identity:
            raise ValueError("Persistent session identity differs")
        if key in self.memory:
            return self.memory[key], "disk"
        # A deliberately tiny RAM budget may not retain the requested entry.
        descriptor = index["entries"][key]
        with np.load(directory/descriptor["name"],allow_pickle=False) as archive:
            arrays=[archive[f"f{i}"][lo:hi].copy() for i,(lo,hi) in enumerate(receipt["entries"][key]["slices"])]
        return arrays,"disk"

    def put(self, ticker, row, coarse, arrays):
        self.put_many(ticker,[row],coarse,arrays)

    def put_many(self, ticker, rows, coarse, arrays):
        """One immutable compressed part per fetched month-page, not per day."""
        self._select_ticker(ticker)
        months = {}
        for row in rows:
            months.setdefault(row["source_date"][:7],[]).append(row)
        for month_rows in months.values():
            identity = self.identity(ticker,month_rows[0],coarse)
            _, marker, directory = self._paths(identity)
            with owned_lock(marker.with_suffix(".lock")):
                index = self._index(marker)
                entries = {};parts=[[] for _ in FRAMES];starts=[0]*5
                for row in month_rows:
                    identity = self.identity(ticker,row,coarse);key=digest(identity)
                    opened,closed=clock(row["source_date"],4),clock(row["source_date"],20)
                    selected=[np.empty(0,dtype=BAR) for _ in FRAMES]
                    for i in (range(3,5) if coarse else range(3)):
                        a=arrays[i];a=a[(a["end_us"]>opened)&(a["end_us"]<=closed)]
                        selected[i]=(a if coarse else a[-self.fine_tail_rows:]).copy()
                    if key in index["entries"]:
                        prior,_=self.get(ticker,row,coarse)
                        if any(a.tobytes()!=b.tobytes() for a,b in zip(prior,selected)):
                            raise ValueError("Recomputed session context differs")
                        continue
                    slices=[]
                    for i,a in enumerate(selected):
                        slices.append([starts[i],starts[i]+len(a)]);starts[i]+=len(a);parts[i].append(a)
                    entries[key]=dict(identity=identity,slices=slices)
                if not entries:
                    continue
                packed=[np.concatenate(p) if p else np.empty(0,dtype=BAR) for p in parts]
                needed=sum(a.nbytes for a in packed)
                # Cover concurrent bounded cache publishers, not only this part.
                if psutil.disk_usage(str(directory)).free<151*1024**3+needed*64:
                    raise OSError("Persistent context would consume disk reserve")
                name="context-"+digest(dict(entries=entries,arrays=[hashlib.sha256(a.tobytes()).hexdigest() for a in packed]))+".npz"
                payload=directory/name
                if not payload.exists():
                    temporary=payload.with_suffix(f".{os.getpid()}.partial")
                    with temporary.open("wb") as stream:
                        np.savez_compressed(stream,**{f"f{i}":a for i,a in enumerate(packed)})
                        stream.flush();os.fsync(stream.fileno())
                    temporary.replace(payload)
                with np.load(payload,allow_pickle=False) as archive:
                    if any(archive[f"f{i}"].tobytes()!=a.tobytes() for i,a in enumerate(packed)):
                        raise ValueError("Persistent context readback differs")
                descriptor=dict(name=name,sha256=file_hash(payload),bytes=payload.stat().st_size)
                receipt=dict(payload=descriptor,entries=entries);receipt["hash"]=digest(receipt)
                receipt_path=payload.with_suffix(".json")
                if receipt_path.exists():
                    if json.loads(receipt_path.read_text())!=receipt:
                        raise ValueError("Immutable context receipt differs")
                else:write_json(receipt_path,receipt)
                # Reopen/hash the immutable part before publishing the pointer.
                self._load(descriptor,directory)
                updated=dict(entries=dict(index["entries"],**{k:descriptor for k in entries}))
                updated["hash"]=digest(updated);write_json(marker,updated)
                self.indices.pop(marker,None)

from contextlib import closing
import sqlite3

import numpy as np

from ..v5.shards.phase1_contract import SAMPLE_DTYPE, digest, file_hash, write_json
from .format import CompactRelease, pack_windows, part_path, unpack
from .run import pack_part, publish_container


def windows():
    rows = np.zeros(3, dtype=SAMPLE_DTYPE)
    rows["timeframe_us"] = 1_000_000
    rows["end_us"] = 1 + np.arange(64)[None, :] + np.array([0, 3, 4])[:, None]
    rows["values"] = rows["end_us"][..., None] / 2
    rows["valid"][..., (0, 5, 17)] = 1
    rows["split"][2] = 1
    return rows


def test_regular_overlapping_windows_are_exact():
    rows = windows()
    packed = pack_windows(rows)
    assert len(packed["pool"]) == 68
    assert len(packed["exceptions"]) == 0
    assert unpack(packed).tobytes() == rows.tobytes()


def test_equal_clocks_with_distinct_bits_do_not_merge():
    rows = windows()
    # Same timestamp but different causal share basis or validity bits.
    rows["values"][1, 0, 0] = -0.0
    rows["values"][2, 1, 0] = +0.0
    rows["values"][0, 5, 0] = -0.0
    rows["valid"][1, 2, 17] = 0
    packed = pack_windows(rows)
    assert len(packed["pool"]) > 68
    assert len(packed["exceptions"])
    assert unpack(packed, 1, 3).tobytes() == rows[1:3].tobytes()


def test_verified_file_and_portable_split_slice(tmp_path):
    source = tmp_path/"legacy"
    source.mkdir()
    out = tmp_path/"portable"
    part_path(out, 0).parent.mkdir(parents=True)
    original = source/"samples-0000.npy"
    rows = windows()
    np.save(original, rows, allow_pickle=False)
    item = (0, original.name, file_hash(original), original.stat().st_size)
    packed = pack_part(source, item)
    result, container = publish_container(out, 0, [packed, (*packed[:-1], 1)])
    assert (result, container) == publish_container(out, 0, [packed, (*packed[:-1], 1)])
    with closing(sqlite3.connect(out/"catalog.sqlite")) as db:
        with db:
            db.executescript("""CREATE TABLE sources(output_bytes, output_sha, container, offset, number);
                                CREATE TABLE entries(split,ordinal,number,start,stop);""")
            db.executemany("INSERT INTO sources VALUES (?,?,?,?,?)", [row[3:] for row in result])
            db.execute("INSERT INTO entries VALUES ('validation',0,1,2,3)")
    seal = dict(version="phase1-compact-v1", catalog_sha256=file_hash(out/"catalog.sqlite"))
    write_json(out/"certificate.json", dict(**seal, hash=digest(seal)))
    moved = out.rename(tmp_path/"moved")
    release = CompactRelease(moved)
    try:
        assert release.read("validation",0).tobytes()==rows[2:].tobytes()
    finally:
        release.close()

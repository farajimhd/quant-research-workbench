"""Independent repair lanes must retain every publication and resume exactly."""

import sqlite3

from .quote_work_queue_v1 import GroupedWork


def test_distinct_jobs_and_complete_receipt_coverage():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE work(path TEXT PRIMARY KEY,done INTEGER DEFAULT 0)")
    paths = [
        f"jobs/{job}/unit{unit}/receipt.json"
        for job in ("a", "b", "c", "d")
        for unit in range(5)
    ]
    db.executemany("INSERT INTO work(path) VALUES(?)", [(p,) for p in paths])
    db.commit()
    queue = GroupedWork(db, 3)
    active = [next(queue) for _ in range(3)]
    seen = []
    while active:
        assert len({p.split("/")[1] for p in active}) == len(active)
        path = active.pop(0)
        seen.append(path)
        db.execute("UPDATE work SET done=1 WHERE path=?", (path,))
        db.commit()
        queue.complete(path)
        following = next(queue, None)
        if following:
            active.append(following)
    queue.close()
    assert sorted(seen) == sorted(paths) and len(seen) == len(set(seen))
    assert db.execute("SELECT count(*) FROM work WHERE done=0").fetchone()[0] == 0


def test_resume_ignores_completed_units_and_supports_windows_paths():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE work(path TEXT PRIMARY KEY,done INTEGER)")
    paths = [
        r"jobs\a\unit0\receipt.json",
        r"jobs\a\unit1\receipt.json",
        r"jobs\b\unit0\receipt.json",
    ]
    db.executemany(
        "INSERT INTO work VALUES(?,?)", [(paths[0], 1), (paths[1], 0), (paths[2], 0)]
    )
    db.commit()
    queue = GroupedWork(db, 2)
    first = next(queue)
    second = next(queue)
    assert {first, second} == set(paths[1:])
    queue.close()

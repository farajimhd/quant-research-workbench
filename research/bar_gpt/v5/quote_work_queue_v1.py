"""Bounded independent source-job lanes for immutable quote repair.

Receipt discovery order clusters many units of one ticker together. Submitting
all of them concurrently makes workers wait on that ticker's candidate-cache
lock. Keep at most one unit per source job active, with a bounded set of jobs.
This changes publication order only, never sample selection or payload content.
"""

from collections import deque


class GroupedWork:
    """Iterate pending SQLite receipt paths using at most ``lanes`` job cursors.

    ``ledger`` is the coordinator's SQLite connection with work(path,done).
    Caller must invoke complete(path) after each future finishes. Completed
    receipts remain authoritative; rebuilding this queue resumes only done=0.
    Both Windows and POSIX relative publication paths are supported.
    """

    def __init__(self, ledger, lanes):
        if lanes < 1:
            raise ValueError("Require a positive number of source-job lanes")
        self.ledger, self.lanes = ledger, lanes
        # Find the second path separator without loading every receipt path
        # into Python. Prefixes identify jobs/<year-id>/ or streams/<id>/.
        rows = ledger.execute("""WITH paths AS (
            SELECT path,replace(path,char(92),'/') normalized FROM work WHERE done=0
          ) SELECT DISTINCT substr(path,1,instr(normalized,'/')+
            instr(substr(normalized,instr(normalized,'/')+1),'/')) prefix
            FROM paths ORDER BY prefix""").fetchall()
        if any(not row[0] for row in rows):
            raise ValueError("Invalid pending publication path")
        self.groups = iter(row[0] for row in rows)
        self.ready, self.active = deque(), {}
        self._fill()

    def _fill(self):
        """Open only enough indexed prefix scans to occupy the configured pool."""
        while len(self.ready) + len(self.active) < self.lanes:
            prefix = next(self.groups, None)
            if prefix is None:
                break
            cursor = self.ledger.execute(
                "SELECT path FROM work WHERE done=0 AND path>=? AND path<? ORDER BY path",
                (prefix, prefix + "\uffff"),
            )
            self.ready.append((prefix, cursor))

    def __iter__(self):
        return self

    def __next__(self):
        while self.ready:
            prefix, cursor = self.ready.popleft()
            row = cursor.fetchone()
            if row is None:
                cursor.close()
                self._fill()
                continue
            self.active[row[0]] = (prefix, cursor)
            return row[0]
        raise StopIteration

    def complete(self, path):
        """Release one lane only after the coordinator has received its result."""
        self.ready.append(self.active.pop(path))

    def close(self):
        """Close all owned read cursors before releasing the coordinator ledger."""
        for _, cursor in [*self.ready, *self.active.values()]:
            cursor.close()
        self.ready.clear()
        self.active.clear()

"""Metrics journal keeps one handle while preserving visible ordered evidence."""

import json
from pathlib import Path

from research.bar_gpt.v5 import reporting_v1


def test_loss_rows_are_visible_in_order_without_reopening_journal(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(reporting_v1, "init_wandb", lambda **kwargs: None)
    monkeypatch.setattr(reporting_v1.Reporter, "_observe", lambda self: None)
    original = Path.open
    writes = []

    def open_file(path, *args, **kwargs):
        if path.name == "metrics.jsonl" and args and args[0] == "a":
            writes.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    reporter = reporting_v1.Reporter(tmp_path, {}, mode="disabled")
    for step in (10, 20, 30):
        reporter.log({"train/loss_sample": 1 / step}, step)
    rows = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    assert [row["step"] for row in rows] == [10, 20, 30]
    assert len(writes) == 1 and not reporter.journal.closed
    reporter.finish()
    assert reporter.journal.closed

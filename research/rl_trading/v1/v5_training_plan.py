"""Forward-only V5 campaign inventory and audit gate.

This layer reads only metadata for the sealed test. It does not open August 26
teacher actions, trajectories, positions, or modeled P&L.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.publish_dynamic_split import TRAIN, DEVELOPMENT, TEST
from src.market_engine.level_book_store import read


@dataclass(frozen=True)
class SessionSource:
    date: str
    split: str
    supervision_root: Path
    feature_root: Path | None
    execution_root: Path | None


@dataclass(frozen=True)
class TrainingPlan:
    train: tuple[SessionSource, ...]
    development: tuple[SessionSource, ...]
    test_sealed: tuple[SessionSource, ...]
    split_hash: str
    audit_hash: str


def _exact_days(entries: list[dict], days) -> bool:
    return [item.get('date') for item in entries] == [str(day) for day in days]


def load_training_plan(split_path: Path, audit_path: Path,
                       feature_roots: dict[str, str],
                       execution_roots: dict[str, str], runtime: Path) -> TrainingPlan:
    """Require a certified 17/2/1 split and a completed 19-day teacher audit."""
    runtime = Path(runtime).resolve()
    split_path, audit_path = Path(split_path).resolve(), Path(audit_path).resolve()
    if not runtime.is_dir() or any(not path.is_relative_to(runtime)
                                   for path in (split_path, audit_path)):
        raise ValueError('V5 campaign manifests must reside under runtime')
    split, audit = read(split_path), read(audit_path)
    if (split.get('version') != 'rl-dynamic-forward-split-v1' or
            split.get('plan_hash') != digest({key: value for key, value
                in split.items() if key != 'plan_hash'}) or
            not _exact_days(split.get('train', []), TRAIN) or
            not _exact_days(split.get('development', []), DEVELOPMENT) or
            not _exact_days(split.get('test_sealed', []), TEST) or
            audit.get('version') != 'rl-dynamic-teacher-comparison-v1' or
            audit.get('report_hash') != digest({key: value for key, value
                in audit.items() if key != 'report_hash'}) or
            audit.get('split_manifest_hash') != file_hash(split_path) or
            len(audit.get('days', ())) != len(TRAIN) + len(DEVELOPMENT) or
            [row.get('date') for row in audit['days']] !=
            [str(day) for day in TRAIN + DEVELOPMENT]):
        raise ValueError('V5 forward split or pretraining teacher audit is incomplete')

    def sources(name: str):
        result = []
        for item in split[name]:
            day = item['date']
            supervision = Path(item['root']).resolve()
            if (not supervision.is_relative_to(runtime) or
                    file_hash(supervision / 'complete.json') != item['complete_hash']):
                raise ValueError(f'V5 supervision certificate changed for {day}')
            feature = (Path(feature_roots[day]).resolve()
                       if day in feature_roots else None)
            execution = (Path(execution_roots[day]).resolve()
                         if day in execution_roots else None)
            if ((name != 'test_sealed' and feature is None) or
                    feature is not None and (not feature.is_relative_to(runtime)
                                             or not (feature / 'complete.json').is_file()) or
                    execution is not None and (not execution.is_relative_to(runtime)
                                               or not (execution / 'complete.json').is_file())):
                raise ValueError(f'V5 feature or execution root unavailable for {day}')
            if name == 'development' and execution is None:
                raise ValueError(f'Development replay grid required for {day}')
            result.append(SessionSource(day, name, supervision, feature, execution))
        return tuple(result)

    # The sealed test inventory is checked by date and certificate only. Its
    # feature/teacher rows are not loaded until after checkpoint selection.
    return TrainingPlan(sources('train'), sources('development'),
                        sources('test_sealed'), file_hash(split_path),
                        file_hash(audit_path))

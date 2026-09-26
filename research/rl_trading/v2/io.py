"""Local artifacts, provenance, and atomic publication."""
from pathlib import Path
import json
import os
import subprocess

from research.rl_trading.v1.common import digest, file_hash, exclusive
from src.runtime_paths import runtime_root

REPO = Path(__file__).resolve().parents[3]


def output_root() -> Path:
    root = runtime_root().resolve()
    if not root.is_dir() or root == REPO or REPO in root.parents:
        raise ValueError('Required external runtime root is unavailable or inside source')
    return root / 'rl-trading' / 'v2'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def code_identity():
    paths = list(Path(__file__).parent.glob('*.py'))
    # V1's certified observation extractor is reused unchanged, never its teachers.
    paths += [REPO / 'research/rl_trading/v1' / (name + '.py') for name in
              ('features', 'arte_source', 'arte_sql', 'reference_features', 'common')]
    paths += [REPO / name for name in ('src/backend/fixed_v7_stream.py',
              'src/backend/structural_v7_seed.py', 'src/market_engine/streaming_level_book.py',
              'src/market_engine/v7_qmd.py')]
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True)
    return dict(git_commit=result.stdout.strip() if result.returncode == 0 else 'unavailable',
                files={str(p.relative_to(REPO)): file_hash(p) for p in paths})

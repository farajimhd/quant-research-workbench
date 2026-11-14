"""User-selected final V4 teacher; no best/latest or earlier-epoch fallback."""
import json
from pathlib import Path
from ..v5.shards.contract import file_hash

EPOCH = 50
RUN = Path('D:/TradingML/runtimes/bar_gpt/encoder-v4-mlp-balanced-20261113')


def select_training_teacher(checkpoint=None, sha256=None, *, root=RUN):
    root = Path(root).resolve()
    path = root / 'checkpoint_epoch_050.pt'
    if checkpoint is not None and Path(checkpoint).resolve() != path:
        raise ValueError('Phase2 v1 training requires the selected V4 epoch50 checkpoint, not best/latest/another run')
    if (root/'STOP').exists():
        raise ValueError('V4 STOP present; final teacher is not released automatically')
    progress = json.loads((root/'progress.json').read_text())
    results_path = root/'results.json'
    results_sha = file_hash(results_path)
    results = json.loads(results_path.read_text())
    history = results.get('history',[])
    if (progress.get('state') != 'complete' or progress.get('epoch') != EPOCH
            or progress.get('epochs') != EPOCH or not history
            or history[-1].get('epoch') != EPOCH
            or history[-1].get('training',{}).get('complete_pass') is not True
            or history[-1].get('validation',{}).get('complete_pass') is not True):
        raise ValueError('Selected V4 epoch50 is not fully trained, validated and complete; no fallback')
    if not path.is_file():raise FileNotFoundError('Completed V4 epoch50 checkpoint is not published')
    actual = file_hash(path)
    if sha256 is not None and actual != sha256:
        raise ValueError('Selected V4 epoch50 SHA256 differs from explicit pin')
    if file_hash(results_path) != results_sha:
        raise ValueError('V4 completion evidence changed during teacher selection')
    return path, actual, dict(policy='user-selected-final-v4-epoch50',run=str(root),epoch=EPOCH,
        checkpoint=str(path),sha256=actual,results_sha256=results_sha,
        encoder='encoder.*',decoder='decoder.* from same file',
        fallback=False,readiness='complete training and full validation; terminal complete; no STOP')

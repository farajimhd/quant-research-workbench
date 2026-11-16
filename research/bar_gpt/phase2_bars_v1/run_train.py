"""Direct future-bar/reference experiment. Default mode is bounded read-only audit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    __package__='research.bar_gpt.phase2_bars_v1'
from pathlib import Path
from ..phase2_v2.run_train import arguments as shared_arguments,main as shared_main
from .config import Config
from .model import Forecaster
from .train import train
from .audit import bounded_check
from .metrics import CONTRACT
from .early_stop import contract as learning_guard_contract


def arguments(argv=None):
    import argparse,sys
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--stop-min-session-fraction',type=float,default=.10)
    parser.add_argument('--stop-stale-session-fraction',type=float,default=.02)
    guard,remaining=parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if not (0<guard.stop_min_session_fraction<=1 and 0<guard.stop_stale_session_fraction<=1):
        parser.error('Session fractions must be finite and in (0,1]')
    if not any(v=='--patience' or v.startswith('--patience=') for v in remaining):
        remaining+=['--patience','24']
    args=shared_arguments(remaining,expected_feature_drop=0.)
    args.stop_min_session_fraction=guard.stop_min_session_fraction
    args.stop_stale_session_fraction=guard.stop_stale_session_fraction
    if args.mode in ('train','profile') and args.device=='cuda':
        from ..phase2_v2.benchmark import active_trainers
        if active_trainers():raise RuntimeError('Another BarGPT trainer owns the GPU; do not contend with the active run')
    return args


def main(argv=None):
    import sys,subprocess
    argv=sys.argv[1:] if argv is None else argv
    print('Command: '+subprocess.list2cmdline([sys.executable,'-B',str(Path(__file__).resolve()),*argv]),flush=True)
    sources=list(Path(__file__).parent.glob('*.py'))
    sources+=list((Path(__file__).parent.parent/'phase2_unmasked_v1').glob('*.py'))
    sources += [Path(__file__).parent.parent/'v5'/name for name in ('pretraining_v1.py','shards/phase1_contract.py')]
    return shared_main(argv,argument_parser=arguments,model_type=Forecaster,model_config=Config,
        version_sources=sources,train_function=train,check_function=bounded_check,validation_contract=CONTRACT,
        validation_guard='full validation every N updates/pass end; session-coverage-gated composite patience',
        learning_guard_contract=learning_guard_contract,
        masking_description='none; all16x512 context content; direct future64x18 bars and seven reference changes')


if __name__=='__main__':main()

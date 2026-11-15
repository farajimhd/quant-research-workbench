"""New unmasked run; shared certified loader, full-pass trainer and online reporting."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    __package__='research.bar_gpt.phase2_unmasked_v1'
from pathlib import Path
from ..phase2_v2.run_train import arguments as shared_arguments,main as shared_main
from .model import Forecaster
from .config import Config


def arguments(argv=None):
    return shared_arguments(argv,expected_feature_drop=0.)


def main(argv=None):
    import sys,subprocess
    argv=sys.argv[1:] if argv is None else argv
    print('Command: '+subprocess.list2cmdline([sys.executable,'-B',str(Path(__file__).resolve()),*argv]),flush=True)
    return shared_main(argv,argument_parser=arguments,model_type=Forecaster,model_config=Config,
        version_sources=Path(__file__).parent.glob('*.py'))


if __name__=='__main__':main()

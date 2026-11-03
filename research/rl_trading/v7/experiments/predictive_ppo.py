"""Frozen forecast-policy replay or own-reward PPO after training-only pretraining."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from pathlib import Path
import sys
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import torch
from research.market_shards.v1.contract import digest,sha
from research.rl_trading.v7 import train
from research.rl_trading.v7.data import TrainingDataset
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from research.rl_trading.v7.experiments.cost_aware import CostEnvironment
from research.rl_trading.v7.experiments.predictive_model import PredictivePolicy


def main(argv=None):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--forecast',type=Path,required=True)
    parser.add_argument('--frozen-forecast',action='store_true')
    parser.add_argument('--train-encoder',action='store_true')
    options,remaining=parser.parse_known_args(argv)
    required=argparse.ArgumentParser(add_help=False)
    required.add_argument('--output',required=True)
    required.add_argument('--dataset',default=DEFAULT_DEST)
    location,_=required.parse_known_args(remaining)
    root=Path(location.output).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Runtime output required')
    dataset=TrainingDataset(location.dataset)
    forecast_hash=sha(options.forecast)
    saved=torch.load(options.forecast,map_location='cpu',weights_only=False)
    contract=saved['contract']
    if (sha(options.forecast)!=forecast_hash or saved['contract_hash']!=digest(contract)
            or contract['training_binding']!=dataset.binding or contract['base_source']!=train.source_identity()):
        raise ValueError('Forecast checkpoint training/source contract mismatch')
    here=Path(__file__).parent
    for name,expected in contract['source'].items():
        if Path(name).name!=name or sha(here/name)!=expected:raise ValueError('Forecast implementation changed')
    if (set(contract['fit_days'])&set(contract['development_days']) or
            contract['fit_days']+contract['development_days']!=dataset.days):
        raise ValueError('Forecast split differs from approved training dates')
    if any(not torch.isfinite(value).all() for value in saved['policy'].values()):
        raise ValueError('Nonfinite forecast checkpoint')
    original_policy,original_environment,original_identity,original_config=train.Policy,train.Environment,train.source_identity,train.Config
    class InitializedConfig(original_config):
        def manifest(self):
            return dict(super().manifest(),version='rl-v7-predictive-init-ppo-v1',
                        labels=dict(initialization='supervised training-only future mark returns',ppo='own account rewards only'),
                        forecast_encoder_trainable=options.train_encoder)
    class InitializedPolicy(PredictivePolicy):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.load_state_dict(saved['policy'])
            if not options.train_encoder:
                for name,parameter in self.named_parameters():
                    parameter.requires_grad_(name.startswith(('buy.','control.','value.')))
    def identity():
        return dict(original_identity(),experiment_sha256=sha(Path(__file__)),
                    predictive_model_sha256=sha(here/'predictive_model.py'),cost_aware_sha256=sha(here/'cost_aware.py'),
                    forecast_sha256=forecast_hash,forecast_contract=saved['contract_hash'],encoder_trainable=options.train_encoder,
                    experiment_recipe='training-only-forecast-init-own-reward-ppo-v1')
    train.Policy=InitializedPolicy;train.Environment=CostEnvironment;train.source_identity=identity;train.Config=InitializedConfig
    try:
        if options.frozen_forecast:
            if '--evaluate' in remaining:raise ValueError('Choose frozen forecast or PPO checkpoint replay')
            root.mkdir(parents=True,exist_ok=True)
            initial=root/'forecast_initial_policy.pt'
            provenance=dict(dataset=dataset.binding,source=identity(),config=InitializedConfig().manifest(),
                            mode='frozen-supervised-forecast-initial-policy',policy_heads_trained=False,
                            forecast_contract=saved['contract_hash'])
            policy=InitializedPolicy()
            if initial.exists():
                prior=torch.load(initial,map_location='cpu',weights_only=False)
                if prior['contract']!=provenance or prior['contract_hash']!=digest(provenance):
                    raise ValueError('Existing initial policy binding mismatch')
                if any(not torch.equal(v,prior['policy'][k]) for k,v in policy.state_dict().items()):
                    raise ValueError('Initial forecast policy weights changed')
            else:
                train.save_checkpoint(initial,dict(contract=provenance,contract_hash=digest(provenance),policy=policy.state_dict()))
            remaining=[*remaining,'--evaluate',str(initial)]
        return train.main(remaining)
    finally:
        train.Policy=original_policy;train.Environment=original_environment;train.source_identity=original_identity;train.Config=original_config


if __name__=='__main__':raise SystemExit(main())

"""Audited V6 teacher initialization followed by genuine quote-aware PPO.

All generated output stays beneath one configured runtime run directory.
Resume restarts the current session from its boundary and retains completed
session checkpoints. No unapproved partial dataset or sealed-test tuning.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import random
import subprocess
import time
import numpy as np
import torch
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.common import digest, file_hash, exclusive, bounds
from research.rl_trading.v6.training_gate import require_dataset
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.identity_map import certify_identity_map, open_identity_map
from research.rl_trading.v6.teacher_data import load_teacher, load_wait_hold_teacher
from research.rl_trading.v6.action_contract import ACTION_VERSION
from research.rl_trading.v6.teacher_selection import teacher_validation_score, selection_key, SELECTION_VERSION
from research.rl_trading.v6.training import train_session
from research.rl_trading.v6.label_timing import CONTRACT as TEACHER_LABEL_TIMING
from research.rl_trading.v6.learning_rate import cosine_warmup
from research.rl_trading.v6.environment_source import ArteExecutionSource
from research.rl_trading.v6.environment import BracketEnvironment
from research.rl_trading.v6.rollout import collect_session, update_session, audit_reconstruction
from research.rl_trading.v6.replay_artifacts import save_replay
from research.rl_trading.v6.prepare_training import _write_json
from research.rl_trading.v6.telemetry import PeriodicProgress


def _commit():
    root=Path(__file__).resolve().parents[3]
    pin=root/'SOURCE_COMMIT.txt'
    return pin.read_text().strip() if pin.is_file() else subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=root,text=True).strip()


def _flatten(prefix, values):
    result={}
    for key,value in values.items():
        name=f'{prefix}/{key}'
        if isinstance(value,dict): result.update(_flatten(name,value))
        elif isinstance(value,(int,float,bool)) or value is None: result[name]=value
    return result


def _checkpoint(path,policy,optimizer,manifest,progress):
    payload={'version':'rl-trading-v6-attention-ppo-checkpoint-1','model':policy.state_dict(),
        'optimizer':optimizer.state_dict(),'manifest_hash':manifest['hash'],'progress':progress,
        'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'numpy_rng':np.random.get_state(),'python_rng':random.getstate()}
    temporary=path.with_suffix('.pt.tmp')
    torch.save(payload,temporary)
    temporary.replace(path)


def _model_causality_audit(policy,device):
    with torch.no_grad():
        scalar=torch.randn(125,37,device=device)
        levels=torch.randn(125,2,5,11,device=device)
        prefix=policy.encoder.encode_listing(scalar,levels)[:120].clone()
        scalar[120:]=999
        levels[120:]=999
        if not torch.equal(prefix,policy.encoder.encode_listing(scalar,levels)[:120]):
            raise ValueError('Temporal encoder failed future-prefix audit')
    return {'future_candle_prefix_invariance':'passed','future_label_observation_fields':0,
            'attention_keys':'only_completed_120_candle_ring',
            'ranking_evidence':'trailing_15_completed_clock_seconds_share_volume'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,help='Defaults to the active audited dataset registry')
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--early-manifest',type=Path,required=True)
    parser.add_argument('--late-manifest',type=Path,required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--teacher-epochs',type=int,default=10)
    parser.add_argument('--teacher-only',action='store_true',
        help='Teacher initialization and per-epoch development label evaluation; no trading replay or PPO')
    parser.add_argument('--ppo-only',action='store_true',
        help='Train PPO from an explicitly selected teacher checkpoint; do not repeat teacher initialization')
    parser.add_argument('--initialize-from',type=Path,
        help='Selected teacher checkpoint for a new PPO run with fresh Adam and explicit source binding')
    parser.add_argument('--teacher-loss',choices=('legacy','balanced-v2'),default='legacy',
        help='Versioned action balancing and fixed per-session block normalization')
    parser.add_argument('--ticker-brackets-root',type=Path,help='Audited oracle brackets for four-action ticker supervision')
    parser.add_argument('--ticker-heads',action='store_true',help='Four local actions, opportunity value and entry-attached brackets; no teacher sizing')
    parser.add_argument('--outside-macd-per-minute',type=float,default=0.,help='Explicit exposure-weighted PPO shaping outside completed-candle positive MACD regime')
    parser.add_argument('--action-contract', choices=('legacy','wait-hold'), default='legacy',
        help='Explicit six-class WAIT and held-ticker HOLD contract with weighted causal label migration')
    parser.add_argument('--action-audit',type=Path,
        help='All-18-session WAIT/HOLD migration certificate, required for the new contract')
    parser.add_argument('--episode-supervision-root',type=Path,
        help='Versioned independent episode window sidecars; fresh teacher-only run, never PPO replay')
    parser.add_argument('--feature-normalization',type=Path,help='Training-only bps/execution feature contract JSON')
    parser.add_argument('--execution-feature-root',type=Path,help='Sparse causal execution features and separate netbps labels')
    parser.add_argument('--ppo-epochs',type=int,default=40)
    parser.add_argument('--ppo-updates',type=int,default=4)
    parser.add_argument('--learning-rate',type=float,default=3e-4)
    parser.add_argument('--teacher-lr-schedule',choices=('fixed','cosine'),default='fixed')
    parser.add_argument('--warmup-epochs',type=float,default=1.)
    parser.add_argument('--minimum-lr-ratio',type=float,default=.1)
    parser.add_argument('--resume-from',type=Path,
        help='Explicit teacher-only continuation from a verified parent run last.pt')
    parser.add_argument('--clocks-per-chunk',type=int,default=32)
    parser.add_argument('--max-orders-per-second',type=int,default=64)
    parser.add_argument('--broker-engine',choices=('reference','tensor-100ms'),default='reference',
        help='Versioned approximate GPU broker uses one proposal per second')
    parser.add_argument('--broker-participation',type=float,default=.1)
    parser.add_argument('--compile-broker',action='store_true',
        help='Compile fixed-shape tensor fill kernels; startup compilation is separate')
    parser.add_argument('--decoder-batch-size',type=int,default=1)
    parser.add_argument('--replay-every',type=int,default=1)
    parser.add_argument('--log-every-seconds',type=float,default=60.)
    parser.add_argument('--luld-root',type=Path,required=True,
        help='Certified modeled LULD sidecar required before training')
    parser.add_argument('--halt-onset-penalty',type=float,default=.10)
    parser.add_argument('--halt-per-minute-penalty',type=float,default=.01)
    parser.add_argument('--terminal-exposure-penalty',type=float,default=.25)
    parser.add_argument('--train-replay-days',type=date.fromisoformat,nargs='+',
        default=[date(2026,7,31),date(2026,8,10),date(2026,8,21)])
    parser.add_argument('--seed',type=int,default=17)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--wandb-project',default='rl-trading-v6')
    args=parser.parse_args(argv)
    if args.teacher_only and args.ppo_only or args.ppo_only != bool(args.initialize_from):
        raise ValueError('PPO-only requires initialize-from and excludes teacher-only')
    if args.initialize_from and args.resume_from:
        raise ValueError('PPO initialization cannot also resume a teacher parent')
    from src.runtime_paths import runtime_root
    runtime=runtime_root().resolve()
    run=args.run_root.resolve()
    if not runtime.is_dir() or not run.is_relative_to(runtime) or any(
            not p.resolve().is_relative_to(runtime) for p in (args.early_manifest,args.late_manifest,args.ledger)):
        raise ValueError('Training input/output roots must be under configured runtime')
    if min(args.teacher_epochs,args.ppo_epochs,args.ppo_updates,args.clocks_per_chunk,
           args.max_orders_per_second,args.replay_every)<1 or args.learning_rate<=0 or args.log_every_seconds<=0:
        raise ValueError('Positive training/rollout limits required')
    if args.dataset is None:
        active=json.loads((runtime/'rl-v6-active-labels.json').read_bytes())
        args.dataset=Path(active['dataset'])
        if file_hash(args.dataset)!=active['sha256']:raise ValueError('Active training dataset changed')
    dataset=require_dataset(args.dataset,runtime_root=runtime)
    from research.rl_trading.v6.opportunity_dataset import VERSION as LABEL_DATASET_VERSION
    if args.action_audit is not None or args.ticker_brackets_root is not None:
        raise ValueError('Old action migration audits and bracket roots cannot enter the current V6 label contract')
    if args.action_contract != 'wait-hold':
        raise ValueError('Current swing labels require WAIT/HOLD transport')
    if not args.ticker_heads:
        raise ValueError('Current swing labels require ticker heads without legacy size/bracket supervision')
    if args.episode_supervision_root is None:
        args.episode_supervision_root=Path(dataset['label_root'])
    elif args.episode_supervision_root.resolve()!=Path(dataset['label_root']).resolve():
        raise ValueError('Episode labels must be the newly certified dataset authority')
    if args.action_contract=='wait-hold' and dataset['version'] != LABEL_DATASET_VERSION:
        if args.action_audit is None or not args.action_audit.resolve().is_relative_to(runtime):
            raise ValueError('WAIT/HOLD requires a runtime all-day migration audit')
        action_audit=json.loads(args.action_audit.read_text())
        records=action_audit.get('label_audits', [])
        if (action_audit.get('status')!='bounded_diagnostic_and_all18_label_audits_complete' or
                action_audit.get('action_version')!=ACTION_VERSION or
                action_audit.get('sealed_test_accessed') is not False or
                action_audit.get('learning',{}).get('dataset_sha256')!=file_hash(args.dataset) or
                len(records)!=len(dataset['days'])):
            raise ValueError('Incomplete or mismatched WAIT/HOLD migration audit')
        for record,entry in zip(records,dataset['days']):
            if (record.get('status')!='passed' or record['day']!=entry['day'] or
                    record['role']!=entry['role'] or
                    record['source_teacher_sha256']!=entry['teacher_sha256'] or
                    record['bank_certificate_sha256']!=entry['bank_certificate_sha256'] or
                    abs(record['effective_weight']-record['original_rows'])>1e-7*max(1,record['original_rows'])):
                raise ValueError('WAIT/HOLD migration audit lost source binding or label weight')
    if args.teacher_epochs < 10:
        raise ValueError('Teacher initialization must run at least 10 epochs')
    if args.teacher_only and args.teacher_epochs > 20:
        raise ValueError('Teacher-only training is capped at 20 epochs')
    if not 0<args.broker_participation<=1 or args.decoder_batch_size<1:
        raise ValueError('Invalid GPU broker or decoder bounds')
    if args.compile_broker:
        if args.broker_engine!='tensor-100ms':
            raise ValueError('Compiled broker requires the tensor-100ms environment')
        compiler=runtime/'rl-v6-compiler-cache'/_commit()
        os.environ['TORCHINDUCTOR_CACHE_DIR']=str(compiler/'inductor')
        os.environ['TRITON_CACHE_DIR']=str(compiler/'triton')
    if args.teacher_lr_schedule=='cosine':
        cosine_warmup(0,args.teacher_epochs,args.learning_rate,args.warmup_epochs,args.minimum_lr_ratio)
    if args.resume_from and (not args.teacher_only or not args.resume_from.resolve().is_relative_to(runtime)):
        raise ValueError('Parent continuation requires a separate teacher-only runtime')
    from research.rl_trading.v6.luld import RiskPenalty
    from research.rl_trading.v6.build_luld import open_sidecar
    risk=RiskPenalty(args.halt_onset_penalty,args.halt_per_minute_penalty,args.terminal_exposure_penalty)
    if not args.luld_root.resolve().is_relative_to(runtime):
        raise ValueError('LULD sidecar must be under configured runtime')
    # Require complete sidecars for every train/development day before any
    # optimizer step, including days not selected for replay diagnostics.
    luld_certificates={str(entry['day']):file_hash(args.luld_root/str(entry['day'])/'complete.json')
                       for entry in dataset['days']}
    for entry in dataset['days']:
        day=date.fromisoformat(str(entry['day']))
        plan=json.loads((Path(entry['bank_root'])/'plan.json').read_text())
        # Validate every day's contents and execution audit before creating
        # an optimizer. Only one sparse sidecar is resident during this audit.
        book,_=open_sidecar(args.luld_root,day,{'build_id':plan['source_build_id'],
            'definition_hash':plan['source_definition_hash']})
        del book
    ranking=MarketAttentionConfig(**dataset['ranking'])
    device=torch.device(args.device)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    torch.backends.cudnn.deterministic=True
    torch.backends.cudnn.benchmark=False
    wait_hold = args.action_contract == 'wait-hold'
    teacher_loader = load_wait_hold_teacher if wait_hold else load_teacher
    episode_certificates={}
    if args.episode_supervision_root:
        from research.rl_trading.v6.opportunity_dataset import load_teacher as load_episode_teacher, DAY_VERSION as EPISODE_VERSION, verify_day
        if not wait_hold:
            raise ValueError('Independent episode labels require fresh WAIT/HOLD teacher-only contract')
        root=args.episode_supervision_root.resolve()
        if not root.is_relative_to(runtime):raise ValueError('Episode labels escaped runtime')
        for entry in dataset['days']:
            path=Path(entry['teacher_root'])/'complete.json'
            proof=json.loads(path.read_text())
            if (proof.get('version')!=EPISODE_VERSION or proof.get('status')!='certified_swing_opportunities' or
                proof.get('bank_certificate_sha256')!=entry['bank_certificate_sha256'] or
                proof.get('day')!=entry['day'] or proof.get('role')!=entry['role'] or
                proof.get('sealed_test_accessed') is not False):
                raise ValueError('All18 episode label certificates must bind to audited banks')
            episode_certificates[entry['day']]=file_hash(path)
            verify_day(Path(entry['teacher_root']),entry['bank_certificate_sha256'])
        market_entries={}
        if dataset.get('market_teacher_dataset'):
            from research.rl_trading.v6.dataset_admission import verify_market
            market_entries={e['day']:e for e in verify_market(dataset,runtime)['days']}
        def teacher_loader(ignored,session,**kwargs):
            entry=next(e for e in dataset['days'] if e['day']==str(session.day))
            market_root=None
            if market_entries:
                market_root=Path(market_entries[str(session.day)]['root'])
            return load_episode_teacher(Path(entry['teacher_root']),session,market_root=market_root,**kwargs)
    policy=RankedBracketActorCritic(config=ranking, wait_hold=wait_hold).to(device)
    ticker_certificates={}
    if args.ticker_heads:
        from research.rl_trading.v6.ticker_heads import TickerDecoder,VERSION as TICKER_VERSION
        from research.rl_trading.v6.ticker_targets import attach_targets
        if not wait_hold or (not args.teacher_only and args.broker_engine!='tensor-100ms'):
            raise ValueError('Ticker heads require WAIT/HOLD transport and tensor PPO')
        if args.teacher_only:
            if not args.episode_supervision_root:
                raise ValueError('Ticker teacher needs certified swing opportunities')
            base_loader=teacher_loader
            # Audit every target binding before creating the optimizer.
            for entry in dataset['days']:
                audited=open_session(Path(entry['bank_root']),runtime_root=runtime,previous_root=Path(entry['previous_root']),split_manifest=Path(entry['split_manifest']) if entry.get('split_manifest') else None)
                labels,_=base_loader(Path(entry['teacher_root']),audited,runtime_root=runtime,audit_development=True)
                _,proof=attach_targets(labels,audited,args.ticker_brackets_root)
                ticker_certificates[entry['day']]=proof
                del labels,audited
            def teacher_loader(ignored,session,**kwargs):
                labels,outcomes=base_loader(ignored,session,**kwargs)
                labels,proof=attach_targets(labels,session,args.ticker_brackets_root)
                if proof!=ticker_certificates[str(session.day)]:raise ValueError('Ticker targets changed after audit')
                return labels,outcomes
        policy.decoder=TickerDecoder(policy.encoder.width).to(device)
    policy.independent_episode_supervision=bool(args.episode_supervision_root)
    feature_contract='legacy'
    execution_certificates={}
    if args.feature_normalization:
        from research.rl_trading.v6.execution_features import VERSION as FEATURE_CONTRACT
        if not args.feature_normalization.resolve().is_relative_to(runtime):raise ValueError('Normalization escaped runtime')
        normalization=json.loads(args.feature_normalization.read_text())
        expected={e['day']:e['bank_certificate_sha256'] for e in dataset['days'] if e['role']=='train'}
        if normalization.get('dataset_sha256')!=file_hash(args.dataset) or normalization.get('training_bank_certificates')!=expected:
            raise ValueError('Normalization must bind to all16 training banks, never development')
        if args.teacher_only and (not args.episode_supervision_root or not args.execution_feature_root):
            raise ValueError('Execution-aware teacher requires independent episode/cost sidecars')
        if not args.teacher_only and args.broker_engine!='tensor-100ms':
            raise ValueError('Execution-aware PPO requires causal tensor broker observations')
        policy.configure_execution_features(normalization);policy.to(device)
        feature_contract=FEATURE_CONTRACT
        if args.teacher_only:
            from research.rl_trading.v6.execution_sidecar import attach_teacher_costs
            execution_root=args.execution_feature_root.resolve()
            if not execution_root.is_relative_to(runtime):raise ValueError('Execution features escaped runtime')
            for entry in dataset['days']:
                proof_path=execution_root/entry['day']/'complete.json'
                proof=json.loads(proof_path.read_text())
                if (proof.get('version')!=FEATURE_CONTRACT or proof.get('status')!='audited_execution_cost_estimates' or
                    proof.get('label_algorithm')!=dataset['algorithm'] or
                    proof.get('label_certificate_sha256')!=entry['teacher_sha256'] or
                    proof.get('bank_certificate_sha256')!=entry['bank_certificate_sha256'] or proof.get('day')!=entry['day'] or
                    proof.get('feature_scope')!='completed_trailing_1s_only' or
                    proof.get('sparse_coverage_version')!='rl-v6-certified-event-sparse-liquidity-v1' or
                    proof.get('luld_certificate')!=luld_certificates[entry['day']] or
                    proof.get('participation')!=args.broker_participation or proof.get('sealed_test_accessed') is not False):
                    raise ValueError('Execution observation provenance differs from audited bank')
                if proof.get('preparation_scope')!='complete_day':raise ValueError('Bounded cost diagnostic is not training data')
                for name in ('features','scores','allocation_netbps'):
                    if file_hash(proof_path.parent/(name+'.parquet'))!=proof['files'][name]['sha256']:
                        raise ValueError('Execution sidecar bytes changed')
                execution_certificates[entry['day']]=file_hash(proof_path)
            original_loader=teacher_loader
            def teacher_loader(ignored,session,**kwargs):
                labels,outcomes=original_loader(ignored,session,**kwargs)
                return attach_teacher_costs(labels,session,execution_root/str(session.day)),outcomes
    elif args.execution_feature_root:
        raise ValueError('Cost observations require the explicit feature normalization contract')
    optimizer=torch.optim.Adam(policy.parameters(),lr=args.learning_rate)
    from research.rl_trading.v6.model import DECODER_VERSION
    manifest={'version':'rl-trading-v6-attention-ppo-run-1','dataset_sha256':file_hash(args.dataset),
        'decoder_version':DECODER_VERSION,
        'feature_contract':feature_contract,
        'feature_normalization_sha256':file_hash(args.feature_normalization) if args.feature_normalization else None,
        'execution_feature_certificates':execution_certificates,
        'ticker_head_contract':TICKER_VERSION if args.ticker_heads else None,
        'ticker_target_certificates':ticker_certificates,
        'outside_macd_per_minute':args.outside_macd_per_minute,
        'ticker_metrics_scope':'independent_ticker_not_global_selection' if args.ticker_heads else None,
        'execution_evidence_version':('rl-v6-certified-event-sparse-liquidity-v1' if feature_contract!='legacy' else None),
        'action_version':TICKER_VERSION if args.ticker_heads else (ACTION_VERSION if wait_hold else 'rl-v6-five-action-v1'),
        'teacher_no_order_labels':'all_causal_held_identities_weighted_1_over_H' if wait_hold else 'portfolio_hold',
        'history_cache':'raw_causal_reprojection_after_teacher_optimizer' if wait_hold else 'detached_projected_history',
        'teacher_selection_version':SELECTION_VERSION,
        'episode_label_certificates':episode_certificates,
        'teacher_label_scope':'independent_episode_flat_and_hypothetical_unit_position' if episode_certificates else 'selected_portfolio_trajectory',
        'teacher_metrics_scope':'local_ticker_alternatives_not_portfolio_selection' if episode_certificates else 'portfolio_action_tokens',
        'action_audit_sha256':file_hash(args.action_audit) if args.action_audit else None,
        'ranking':asdict(ranking),'source_commit':_commit(),
        'label_algorithm':dataset['algorithm'],'label_raw_value_units':dataset['raw_value_units'],
        'teacher_label_timing':TEACHER_LABEL_TIMING,
        'label_publication_audit_sha256':dataset['publication_audit_sha256'],
        'config':{k:([str(item) for item in v] if isinstance(v,list) else str(v) if isinstance(v,Path) else v)
                  for k,v in vars(args).items() if k not in ('resume','audit_only')},
        'teacher_role':'candle_only_actor_initialization',
        'environment_version':'rl-v6-tensor-participation-100ms-v1' if args.broker_engine=='tensor-100ms' else 'reference-quote-oms',
        'decision_cadence':'one_proposal_per_second' if args.broker_engine=='tensor-100ms' else 'bounded_same_clock_proposals',
        'validation_contract':'development_teacher_labels_trading_validation_pending' if args.teacher_only else 'trading_replay',
        'reward':'quote_delta_equity_minus_separate_modeled_halt_and_terminal_exposure_shaping_v2',
        'risk_penalty':asdict(risk),'luld_certificates':luld_certificates,
        'holding_observation_version':'11_fields_modeled_halt_flag_and_age',
        'research_price_increment':.0001,'price_increment_authority':'canonical_precision_scenario_not_exchange_tick',
        'wandb_key_present':bool(os.environ.get('WANDB_API_KEY'))}
    manifest['hash']=digest(manifest)
    initialization=None
    if args.initialize_from:
        initial=args.initialize_from.resolve()
        if not initial.is_relative_to(runtime):
            raise ValueError('Teacher initialization checkpoint escaped runtime')
        parent=json.loads((initial.parent/'manifest.json').read_text())
        selected=json.loads((initial.parent/'teacher-selection.json').read_text())
        completed=json.loads((initial.parent/'complete.json').read_text())
        initialization=torch.load(initial,map_location=device,weights_only=False)
        if (initialization['manifest_hash']!=parent['hash'] or
                completed.get('status')!='teacher_trained_label_evaluated_trading_validation_pending' or
                completed.get('manifest_hash')!=parent['hash'] or
                parent.get('decoder_version')!=DECODER_VERSION or
                parent.get('execution_evidence_version')!=manifest['execution_evidence_version'] or
                parent.get('feature_contract','legacy')!=feature_contract or
                parent.get('feature_normalization_sha256')!=manifest.get('feature_normalization_sha256') or
                parent.get('action_version')!=manifest['action_version'] or
                parent.get('teacher_label_timing')!=manifest['teacher_label_timing'] or
                parent['dataset_sha256']!=manifest['dataset_sha256'] or
                parent['luld_certificates']!=manifest['luld_certificates'] or
                parent.get('teacher_selection_version')!=SELECTION_VERSION or
                selected.get('checkpoint_sha256')!=file_hash(initial) or
                selected.get('score',{}).get('exact_entry_f1',0)<=0 or
                Path(selected['checkpoint']).resolve()!=initial or
                selected.get('sealed_test_accessed') is not False):
            raise ValueError('PPO initialization lacks matching development-selected teacher authority')
        policy.load_state_dict(initialization['model'],strict=True)
        manifest['initial_teacher_checkpoint_sha256']=file_hash(initial)
        manifest['initial_teacher_manifest_hash']=parent['hash']
        manifest['hash']=digest({k:v for k,v in manifest.items() if k!='hash'})
    parent_payload=None
    if args.resume_from:
        parent_manifest=json.loads((args.resume_from.parent/'manifest.json').read_text())
        parent_payload=torch.load(args.resume_from,map_location=device,weights_only=False)
        if parent_payload['manifest_hash']!=parent_manifest['hash']:
            raise ValueError('Parent checkpoint manifest mismatch')
        if parent_manifest.get('teacher_label_timing') != manifest['teacher_label_timing']:
            raise ValueError('Parent teacher feature timing differs; fresh compatible training required')
        if (parent_manifest.get('execution_evidence_version')!=manifest['execution_evidence_version'] or
                parent_manifest.get('feature_contract','legacy')!=feature_contract or
                parent_manifest.get('feature_normalization_sha256')!=manifest.get('feature_normalization_sha256')):
            raise ValueError('Parent feature units differ; fresh compatible training required')
        if parent_manifest.get('decoder_version') != DECODER_VERSION:
            raise ValueError('Parent decoder activation contract differs; '
                'explicit validated migration is required before continuation')
        if parent_manifest.get('action_version', 'rl-v6-five-action-v1') != manifest['action_version']:
            raise ValueError('Parent WAIT/HOLD head and label contract differs; start a fresh audited run')
        ignored={'run_root','teacher_lr_schedule','warmup_epochs','minimum_lr_ratio','resume_from','teacher_loss'}
        current=manifest['config']; previous={'broker_engine':'reference','compile_broker':False,
            'broker_participation':.1,'decoder_batch_size':1,'action_contract':'legacy',**parent_manifest['config']}
        if ({k:v for k,v in current.items() if k not in ignored} !=
                {k:v for k,v in previous.items() if k not in ignored} or
                manifest['dataset_sha256']!=parent_manifest['dataset_sha256'] or
                manifest['luld_certificates']!=parent_manifest['luld_certificates'] or
                parent_payload['progress']['phase']!='teacher'):
            raise ValueError('Parent continuation changed data or non-schedule configuration')
        manifest['parent_checkpoint_sha256']=file_hash(args.resume_from)
        manifest['parent_manifest_hash']=parent_manifest['hash']
        manifest['hash']=digest({k:v for k,v in manifest.items() if k!='hash'})
        if args.resume:
            parent_payload=None  # Subsequent resumes use this run's own last.pt.
    run.mkdir(parents=True,exist_ok=True)
    progress={'phase':'ppo' if args.ppo_only else 'teacher','epoch':0,'day_index':0,'wandb_step':0}
    logger=None
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    with exclusive(run/'run.lock'):
        if (run/'manifest.json').is_file():
            if json.loads((run/'manifest.json').read_text())!=manifest:
                raise ValueError('Run root belongs to another dataset/config/source')
            if not args.resume and not args.audit_only:
                raise ValueError('Existing run requires explicit --resume')
        else: _write_json(run/'manifest.json',manifest)
        last=run/'last.pt'
        if args.resume or parent_payload is not None:
            payload=parent_payload if parent_payload is not None else torch.load(last,map_location=device,weights_only=False)
            if parent_payload is None and payload['manifest_hash']!=manifest['hash']:
                raise ValueError('Resume checkpoint belongs to another audited run')
            policy.load_state_dict(payload['model'])
            optimizer.load_state_dict(payload['optimizer'])
            progress=payload['progress']
            if progress['phase'] in ('complete','stopped_validation_deterioration'):
                raise ValueError('Training already completed; use selected checkpoint replay, not a second run')
            torch.set_rng_state(payload['torch_rng'].cpu())
            if payload['cuda_rng']: torch.cuda.set_rng_state_all([r.cpu() for r in payload['cuda_rng']])
            np.random.set_state(payload['numpy_rng']); random.setstate(payload['python_rng'])
        def open_day(entry):
            return open_session(Path(entry['bank_root']),runtime_root=runtime,previous_root=Path(entry['previous_root']),split_manifest=Path(entry['split_manifest']) if entry.get('split_manifest') else None)
        def evidence(session):
            key=str(session.day)
            reader=arte_source.reader(threads=2)
            try:
                source=arte_source.load_build(args.early_manifest if session.day<=date(2026,8,17) else args.late_manifest,
                                             args.ledger,[session.day])
                plan=json.loads((session.root/'plan.json').read_text())
                if source['build_id']!=plan['source_build_id'] or source['definition_hash']!=plan['source_definition_hash']:
                    raise ValueError('Replay source differs from certified feature bank')
                identity=run/'identity'/key
                if not (identity/'complete.json').is_file():
                    population,proof=arte_source.population(reader,source,session.day)
                    certify_identity_map(session,population,proof['snapshot_hash'],identity,runtime_root=runtime)
                tickers=open_identity_map(identity,session,runtime_root=runtime)
                provider=ArteExecutionSource(reader,source,args.ledger,session.day,end_us=bounds(session.day)[1])
                provider.luld,provider.luld_certificate = open_sidecar(args.luld_root,session.day,source)
                return provider,tickers
            except Exception:
                reader.close(); raise
        def log(prefix,metrics):
            metrics={**metrics,'learning_rate':optimizer.param_groups[0]['lr']}
            progress['wandb_step']+=1
            if logger: logger.log(_flatten(prefix,metrics),step=progress['wandb_step'])
            print(json.dumps({'phase':prefix,'metrics':metrics},default=str),flush=True)
            with (run/'metrics.jsonl').open('a',encoding='utf-8') as out:
                out.write(json.dumps({'step':progress['wandb_step'],'phase':prefix,'metrics':metrics},default=str)+'\n')
        def tensor_collect(session,provider,tickers,*,max_clocks=None,deterministic=False):
            from research.rl_trading.v6.tensor_broker import TensorBroker,BrokerConfig
            from research.rl_trading.v6.tensor_rollout import collect_tensor_session
            from research.rl_trading.v6.broker_shards import BrokerShards,VERSION
            start=next(session.candle_events()).close_us
            finish=bounds(session.day)[1] if max_clocks is None else min(bounds(session.day)[1],start+max_clocks*1_000_000)
            namespace=digest((VERSION,provider.source['build_id'],tickers,
                [(t,provider.attempts[t]) for t in tickers]))[:20]
            provider.expose_execution_features=feature_contract!='legacy'
            shards=BrokerShards(provider,tickers,runtime/'rl-v6-broker-shards'/str(session.day)/namespace,
                                runtime_root=runtime)
            broker=TensorBroker(len(tickers),device=device,config=BrokerConfig(
                participation=args.broker_participation,risk=risk,outside_macd_per_minute=args.outside_macd_per_minute))
            broker.expose_cost_features=feature_contract!='legacy'
            if args.compile_broker:
                broker.compile_step()
            pulse_tensor=PeriodicProgress(lambda values:log('progress/tensor_rollout',
                {**values,'day':str(session.day)}),seconds=args.log_every_seconds)
            with closing(shards.buckets(start,finish,device=device,luld=provider.luld)) as tape:
                collection=collect_tensor_session(policy,session,broker,tape,
                    device=device,max_clocks=max_clocks,deterministic=deterministic,
                    progress_callback=lambda clock,count:pulse_tensor({'close_us':clock,'policy_steps':count}))
            return collection
        def pulse(prefix,day,epoch):
            return PeriodicProgress(lambda values:log(prefix,{**values,'day':str(day),'epoch':epoch+1}),
                                    seconds=args.log_every_seconds)
        try:
            audit=_model_causality_audit(policy,device)
            # Fresh non-learning smoke verifies the real quote collector and
            # recurrent reconstruction before any teacher/PPO optimizer step.
            if not args.teacher_only:
                session=open_day(dataset['days'][0])
                provider,tickers=evidence(session)
                try:
                    if args.broker_engine=='tensor-100ms':
                        env=tensor_collect(session,provider,tickers,max_clocks=5)
                        frames,steps,smoke=env.frames,env.steps,env.summary
                    else:
                        env=BracketEnvironment(tickers,provider,luld=provider.luld,risk_penalty=risk)
                        frames,steps,smoke=collect_session(policy,session,env,device=device,max_clocks=5,max_orders_per_second=4)
                    audit.update(real_reconstruction=audit_reconstruction(policy,session,frames,device=device),
                                 smoke_metrics=smoke,execution_evidence=provider.certificate())
                finally: provider.reader.close()
                del session,frames,steps,env,provider
            else:
                audit['trading_validation']='pending_execution_price_coverage'
            _write_json(run/'model-audit.json',{'status':'passed','source_commit':manifest['source_commit'],
                'dataset_sha256':manifest['dataset_sha256'],**audit})
            if args.audit_only:
                print(json.dumps({'status':'all_training_launch_audits_passed','run_root':str(run)}),flush=True)
                return 0
            import wandb
            logger=wandb.init(project=args.wandb_project,name=run.name,id=manifest['hash'][:16],
                              resume='allow',mode='online',dir=str(run),config=manifest)
            (run/'metrics.jsonl').touch(exist_ok=True)
            logger.save(str(run/'metrics.jsonl'),base_path=str(run),policy='live')
            for filename in ('manifest.json','model-audit.json'):
                logger.save(str(run/filename),base_path=str(run),policy='now')
            # Smoke sampling is diagnostic; production starts at exact seed
            # (or saved RNG) regardless of how often audit-only was invoked.
            if args.resume or parent_payload is not None:
                torch.set_rng_state(payload['torch_rng'].cpu())
                if payload['cuda_rng']: torch.cuda.set_rng_state_all([r.cpu() for r in payload['cuda_rng']])
            else:
                torch.manual_seed(args.seed)
            train_days=[e for e in dataset['days'] if e['role']=='train']
            dev_days=[e for e in dataset['days'] if e['role']=='development']
            diagnostics=[e for e in train_days if date.fromisoformat(str(e['day'])) in args.train_replay_days]
            if len(diagnostics)!=len(set(args.train_replay_days)):
                raise ValueError('Training replay diagnostics must select audited training days')
            phases=(('ppo',args.ppo_epochs),) if args.ppo_only else (('teacher',args.teacher_epochs),) if args.teacher_only else (('teacher',args.teacher_epochs),('ppo',args.ppo_epochs))
            for phase,epochs in phases:
                if phase=='teacher' and progress['phase']=='ppo': continue
                epoch_start=progress['epoch'] if progress['phase']==phase else 0
                for epoch in range(epoch_start,epochs):
                    day_start=progress['day_index'] if progress['phase']==phase and epoch==epoch_start else 0
                    for day_index,entry in enumerate(train_days[day_start:],start=day_start):
                        started=time.perf_counter()
                        log('progress/session_start',{'day':entry['day'],'phase':phase,'epoch':epoch+1})
                        session=open_day(entry)
                        if phase=='teacher':
                            start_clock,end_clock=bounds(session.day)
                            def teacher_rate(clock):
                                position=epoch+(day_index+(clock-start_clock)/(end_clock-start_clock))/len(train_days)
                                return cosine_warmup(position,args.teacher_epochs,args.learning_rate,
                                    args.warmup_epochs,args.minimum_lr_ratio)
                            decisions,outcomes=teacher_loader(Path(entry['teacher_root']),session,runtime_root=runtime)
                            result=asdict(train_session(policy,optimizer,session,decisions,outcomes,
                                device=device,clocks_per_chunk=args.clocks_per_chunk,
                                teacher_loss=args.teacher_loss,
                                learning_rate_for_clock=teacher_rate if args.teacher_lr_schedule=='cosine' else None,
                                progress_callback=pulse('progress/teacher',session.day,epoch)))
                            result['learning_rate']=optimizer.param_groups[0]['lr']
                            del decisions,outcomes
                        else:
                            provider,tickers=evidence(session)
                            try:
                                if args.broker_engine=='tensor-100ms':
                                    env=tensor_collect(session,provider,tickers)
                                    frames,steps,result=env.frames,env.steps,env.summary
                                else:
                                    env=BracketEnvironment(tickers,provider,luld=provider.luld,risk_penalty=risk)
                                    frames,steps,result=collect_session(policy,session,env,device=device,
                                        max_orders_per_second=args.max_orders_per_second,
                                        progress_callback=pulse('progress/rollout',session.day,epoch))
                                result['ppo']=update_session(policy,optimizer,session,frames,steps,device=device,
                                    epochs=args.ppo_updates,clocks_per_chunk=args.clocks_per_chunk,
                                    decoder_batch_size=args.decoder_batch_size,
                                    bootstrap=env.bootstrap if args.broker_engine=='tensor-100ms' else None,
                                    batch_candle_projection=args.broker_engine=='tensor-100ms',
                                    progress_callback=pulse('progress/ppo',session.day,epoch))
                                result['execution_evidence']=provider.certificate()
                                del frames,steps,env
                            finally: provider.reader.close()
                        result.update(day=str(session.day),epoch=epoch+1,elapsed_seconds=time.perf_counter()-started)
                        log(f'train/{phase}',result)
                        progress.update(phase=phase,epoch=epoch,day_index=day_index+1)
                        _checkpoint(last,policy,optimizer,manifest,progress)
                        del session
                    # Retain the completed-session boundary until replay and
                    # selection finish. A crash must not skip this validation.
                    progress.update(phase=phase,epoch=epoch,day_index=len(train_days))
                    checkpoint=run/f'{phase}-epoch-{epoch+1:03d}.pt'
                    _checkpoint(checkpoint,policy,optimizer,manifest,progress)
                    _checkpoint(last,policy,optimizer,manifest,progress)
                    if args.teacher_only:
                        teacher_summaries=[]
                        for entry in dev_days:
                            session=open_day(entry)
                            decisions,outcomes=teacher_loader(Path(entry['teacher_root']),session,runtime_root=runtime,audit_development=True)
                            result=asdict(train_session(policy,None,session,decisions,outcomes,
                                device=device,clocks_per_chunk=args.clocks_per_chunk,evaluation=True,
                                progress_callback=pulse('progress/teacher_validation',session.day,epoch)))
                            result.update(day=str(session.day),epoch=epoch+1)
                            teacher_summaries.append(result)
                            log('validation/teacher',result)
                            del session,decisions,outcomes
                        score=teacher_validation_score(teacher_summaries,development_days=[e['day'] for e in dataset['days'] if e['role']=='development'])
                        selection=run/'teacher-selection.json'
                        previous=json.loads(selection.read_text()) if selection.is_file() else None
                        if previous is None or selection_key(score)>selection_key(previous['score']):
                            _write_json(selection,{'status':'selected_on_development_teacher_labels',
                                'checkpoint':str(checkpoint),'checkpoint_sha256':file_hash(checkpoint),
                                'epoch':epoch+1,'score':score,'sealed_test_accessed':False,
                                'development_summaries':teacher_summaries})
                        log('validation/teacher_selection',score)
                        logger.save(str(checkpoint),base_path=str(run),policy='now')
                    elif (epoch+1)%args.replay_every==0:
                        summaries=[]
                        for entry in dev_days+diagnostics:
                            session=open_day(entry)
                            provider,tickers=evidence(session)
                            try:
                                if args.broker_engine=='tensor-100ms':
                                    env=tensor_collect(session,provider,tickers,deterministic=True)
                                    frames,steps,summary=env.frames,env.steps,env.summary
                                else:
                                    env=BracketEnvironment(tickers,provider,luld=provider.luld,risk_penalty=risk)
                                    frames,steps,summary=collect_session(policy,session,env,device=device,
                                        max_orders_per_second=args.max_orders_per_second,deterministic=True,
                                        progress_callback=pulse('progress/replay',session.day,epoch))
                                quote_cert=run/'quote-evidence'/checkpoint.stem/f'{session.day}.json'
                                _write_json(quote_cert,provider.certificate())
                                if args.broker_engine=='tensor-100ms':
                                    from research.rl_trading.v6.tensor_artifacts import save_tensor_replay
                                    replay_root,_=save_tensor_replay(env,session,checkpoint,runtime_root=runtime,
                                        source_commit=manifest['source_commit'],quote_evidence_certificate=quote_cert)
                                else:
                                    replay_root,_=save_replay(env.journal,session,checkpoint,runtime_root=runtime,
                                        source_commit=manifest['source_commit'],quote_evidence_certificate=quote_cert)
                                summary.update(day=str(session.day),replay_root=str(replay_root))
                                log('replay/development' if entry['role']=='development' else 'replay/train_in_sample',summary)
                                if logger:
                                    artifact=wandb.Artifact(f'{checkpoint.stem}-{session.day}',type='model-replay-ledger')
                                    for name in ('orders.parquet','positions.parquet','equity.parquet','metrics.json','complete.json'):
                                        artifact.add_file(str(replay_root/name))
                                    logger.log_artifact(artifact)
                                if entry['role']=='development': summaries.append(summary)
                                del frames,steps,env
                            finally: provider.reader.close()
                            del session
                        net=sum(s['modeled_net_profit'] for s in summaries)
                        validation_log=run/'validation-history.json'
                        history=json.loads(validation_log.read_text()) if validation_log.is_file() else []
                        history=[r for r in history if not (r['phase']==phase and r['epoch']==epoch+1)]
                        history.append({'phase':phase,'epoch':epoch+1,'net':net,
                            'fees':sum(s['modeled_fees'] for s in summaries),
                            'turnover':sum(s['turnover_dollars'] for s in summaries)})
                        _write_json(validation_log,history)
                        eligible=net>0 and sum(s['buy_fill_orders'] for s in summaries)>0 and all(s['terminally_flat'] for s in summaries)
                        selection=run/'selection.json'
                        old=json.loads(selection.read_text()) if selection.is_file() else None
                        if eligible and (old is None or net>old['development_net_profit']):
                            _write_json(selection,{'status':'selected_on_development','checkpoint':str(checkpoint),
                                'checkpoint_sha256':file_hash(checkpoint),'development_net_profit':net,
                                'development_summaries':summaries,'sealed_test_accessed':False})
                        recent=[r for r in history if r['phase']=='ppo'][-4:]
                        if phase=='ppo' and len(recent)==4 and all(
                                after['net']<before['net'] for before,after in zip(recent,recent[1:])):
                            progress.update(phase='stopped_validation_deterioration')
                            _checkpoint(last,policy,optimizer,manifest,progress)
                            _write_json(run/'stop.json',{'reason':'three_consecutive_development_net_declines',
                                'recent_validation':recent,'selected_checkpoint_preserved':bool(old),
                                'increasing_fees_and_turnover':all(b['fees']>a['fees'] and b['turnover']>a['turnover']
                                    for a,b in zip(recent,recent[1:]))})
                            return 0
                    progress.update(phase=phase,epoch=epoch+1,day_index=0)
                    _checkpoint(last,policy,optimizer,manifest,progress)
                if phase=='teacher' and not args.teacher_only:
                    progress.update(phase='ppo',epoch=0,day_index=0)
                    _checkpoint(last,policy,optimizer,manifest,progress)
            progress.update(phase='complete')
            _checkpoint(last,policy,optimizer,manifest,progress)
            _write_json(run/'complete.json',{'status':'teacher_trained_label_evaluated_trading_validation_pending' if args.teacher_only else 'trained_and_development_evaluated',
                'manifest_hash':manifest['hash'],'selected_checkpoint':str(run/'selection.json') if (run/'selection.json').is_file() else None,
                'heldout_replay_pending':True})
        except Exception as error:
            _write_json(run/'failure.json',{'type':type(error).__name__,'message':str(error),
                'progress':progress,'resume_boundary':'last completed session checkpoint'})
            raise
        finally:
            if logger: logger.finish()
    return 0


if __name__=='__main__':
    raise SystemExit(main())

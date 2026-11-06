"""Explicit immutable V67 training-receipt reuse for a new founder admission."""
import json
from pathlib import Path
from .distributed_assets import sha


def verify_replay_sources(code):
    """Reject any replay/input change beyond namespace-only relocation."""
    scoring={'campaign_progress.py','campaign_runner.py','controller_metrics.py',
             'founder_readmission.py','full_common.py','generation_flow.py','global_contract.py',
             'identity.py','prepare_selection_restart.py','restored_summary.py','resume_authority.py',
             'robust_score.py','run_train.py','session_pipeline.py','supervision.py','ticker_selection.py',
             'profile_global.py'}
    source=Path(code)/'research/vectorized_backtest/v67'
    for original in source.iterdir():
        if original.suffix not in ('.py','.json') or original.name in scoring:continue
        current=Path(__file__).parent/original.name
        a=original.read_bytes().replace(b'\r\n',b'\n')
        b=current.read_bytes().replace(b'\r\n',b'\n').replace(b'research.vectorized_backtest.v68',b'research.vectorized_backtest.v67')
        if original.name in ('compact_execution.py','compact_replay.py'):
            import ast
            class WithoutCaptureTracking(ast.NodeTransformer):
                def visit_FunctionDef(self,node):
                    if node.name in ('_one','advance') and node.args.args[-1].arg=='capture_state':
                        node.args.args.pop()
                        if node.args.defaults:node.args.defaults.pop()
                    return self.generic_visit(node)
                def visit_If(self,node):
                    if ast.dump(node.test)==ast.dump(ast.parse('capture_state is not None',mode='eval').body):return None
                    return self.generic_visit(node)
                def visit_Call(self,node):
                    if isinstance(node.func,ast.Name) and node.func.id in ('_one','advance'):
                        node.args=[arg for arg in node.args if not (isinstance(arg,ast.Name) and arg.id=='capture_state')]
                    return self.generic_visit(node)
                def visit_Assign(self,node):
                    if len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('capture_state','transfer_buffers'):return None
                    return self.generic_visit(node)
                def visit_Name(self,node):
                    if node.id=='transfer_buffers':node.id='TRANSFER_BUFFERS'
                    return node
                def visit_ImportFrom(self,node):
                    if node.module=='performance':node.names=[name for name in node.names if name.name!='transfer_capacity']
                    return None if node.module=='opportunity_cost' else node
                def visit_Expr(self,node):
                    if (isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name)
                        and node.value.func.id=='apply' and any(k.arg=='captured_counts' for k in node.value.keywords)):return None
                    return self.generic_visit(node)
            def without_capture(raw):
                return ast.dump(WithoutCaptureTracking().visit(ast.parse(raw.decode('utf8'))),include_attributes=False)
            a,b=without_capture(a),without_capture(b)
        if original.name=='performance.py':
            import ast
            class WithoutQueueSizing(ast.NodeTransformer):
                def visit_FunctionDef(self,node):
                    return None if node.name=='transfer_capacity' else self.generic_visit(node)
                def visit_Call(self,node):
                    if isinstance(node.func,ast.Name) and node.func.id=='transfer_capacity':
                        return ast.Name(id='TRANSFER_BUFFERS',ctx=ast.Load())
                    return self.generic_visit(node)
            def without_queue_sizing(raw):
                return ast.dump(WithoutQueueSizing().visit(ast.parse(raw.decode('utf8'))),include_attributes=False)
            a,b=without_queue_sizing(a),without_queue_sizing(b)
        if original.name in ('global_runner.py','session_lanes.py','io_pipeline.py'):
            import ast
            def without_admission(raw):
                tree=ast.parse(raw.decode('utf8'))
                for node in tree.body:
                    if isinstance(node,ast.FunctionDef) and node.name=='release_plans':
                        node.body=[statement for statement in node.body if not
                            (isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call)
                             and ast.dump(statement.value.func)==ast.dump(ast.parse('_worker.staging.release_completed',mode='eval').body))]
                    if isinstance(node,ast.ClassDef) and node.name=='TransferStaging':
                        node.body=[method for method in node.body if not
                            (isinstance(method,ast.FunctionDef) and method.name in ('stage','activate','close','_release_registered'))]
                        for method in node.body:
                            if isinstance(method,ast.FunctionDef) and method.name=='release_completed':
                                method.body=[statement for statement in method.body if not
                                    (isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call)
                                     and ast.dump(statement.value.func)==ast.dump(ast.parse('self._release_registered',mode='eval').body))]
                    if isinstance(node,ast.ClassDef) and node.name=='Global':
                        node.body=[method for method in node.body if not
                            (isinstance(method,ast.FunctionDef) and method.name=='prepare_resident_sessions')]
                        for method in node.body:
                            if isinstance(method,ast.FunctionDef) and method.name=='__init__':
                                for statement in method.body:
                                    if (isinstance(statement,ast.Assign) and len(statement.targets)==1
                                        and isinstance(statement.targets[0],ast.Attribute)
                                        and statement.targets[0].attr=='host_load_slots'):
                                        statement.value=ast.Constant(value='startup-load-semaphore')
                return ast.dump(tree,include_attributes=False)
            a,b=without_admission(a),without_admission(b)
        if a!=b:raise ValueError('Founder replay/input implementation changed: '+original.name)


def verify_cache_bindings(job,bound):
    original=bound.get('result_caches',{});current=job.get('result_cache_transition',{})
    if current==original:return
    # Later source-only resumes add current replay indexes; original founder
    # authority stays immutable, and new indexes must belong to the pinned source.
    from .resume_authority import global_predecessor
    previous=global_predecessor(job)
    prior=previous.get('result_cache_transition',{})
    if previous.get('founder_readmission')!=bound:
        raise ValueError('Founder readmission authority changed on resume')
    if any(current.get(k)!=v for mapping in (original,prior) for k,v in mapping.items()):
        raise ValueError('Founder/prior cache bindings changed')
    if any(v.get('source')!=previous['source_sha256'] for k,v in current.items() if k not in prior):
        raise ValueError('New cache binding is not from the pinned predecessor source')


def verify(job):
    bound=job['founder_readmission']
    if bound.get('version')!='v68-saved-founder-rescore-v1':raise ValueError('Unknown founder readmission')
    path=Path(bound['predecessor_job'])
    if sha(path)!=bound['predecessor_job_sha256']:raise ValueError('Founder predecessor changed')
    old=json.loads(path.read_bytes())
    if old['version']!='v67-global-v1' or old['initial_multiplier']!=6:raise ValueError('Sixfold V67 founders required')
    for key in ('training_sha256','validation_sha256','training_days','validation_days','asset_sha256',
                'execution','objective','concentration_coefficient','selection','founder_pool',
                'initialization_probe_sha256','strategy_contract','seed','population','initial_multiplier'):
        if job[key]!=old[key]:raise ValueError('Founder readmission changes '+key)
    if old.get('score_version')!='saved-1b-entry-profit-and-request-penalties-v2':raise ValueError('Expected approved V67 objective')
    if old['supervision']['sha256']!=job['supervision']['sha256']:raise ValueError('Founder label authority changed')
    founders=Path(old['campaign'])/'fresh-founders.json'
    if str(founders)!=bound['founders'] or sha(founders)!=bound['founders_sha256']:raise ValueError('Founder genomes changed')
    expected={f'{role}/0/{day}' for role in ('training','validation') for day in old[role+'_days']}
    if set(bound['completed_replays'])!=expected:raise ValueError('Complete split-bound founder receipts required')
    for role in ('training','validation'):
        for day in old[role+'_days']:
            item=bound['completed_replays'][f'{role}/0/{day}']
            if Path(item['path'])!=Path(old['campaign'])/f'phase-2/replays/{role}/generation-0000'/day/'receipt.json':
                raise ValueError('Founder receipt path changed')
    verify_cache_bindings(job,bound)
    verify_replay_sources(old['code'])
    return old


def seed(job):
    old=verify(job);state=json.loads(Path(job['founder_readmission']['founders']).read_bytes())
    if state['source_sha256']!=old['source_sha256'] or state['validation_opened'] or len(state['population'])!=6144:
        raise ValueError('Founder state authority changed')
    if any(v is not None for v in state['sources']):raise ValueError('Imported expert founders forbidden')
    from .genome import Individual
    from .founder_pool import validate
    for p in state['population']:validate(Individual.restore(p))
    return dict(state,source_sha256=job['source_sha256'],readmission=job['founder_readmission'])

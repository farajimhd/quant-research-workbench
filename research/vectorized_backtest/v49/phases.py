"""One unrestricted local phase; no phase0 and no specialist promotion."""
from pathlib import Path
from research.vectorized_backtest.v42.publication import write_json

def execute(config,day,root,owner):
    from .local_runner import run_session
    root=Path(root);generations=config['phase1_generations'];output=root/'phase-1'
    if (root/'STOP').exists():return dict(status='interrupted',phase=1)
    write_json(output/'manifest.json',dict(days=[day],local_generations=generations,phase=1,parent_campaign=str(root),source_sha256=owner['source_sha256']))
    write_json(root/'active.json',dict(owner,campaign=str(output),phase=1,generations=generations,total_generations=generations))
    result=run_session(dict(config,phase=1),day,output/'local'/day,generations,1024,2020,initial_multiplier=6)
    if result['status']!='complete':return dict(result,phase=1)
    write_json(root/'complete.json',dict(status='complete',phase1_generations=generations,phase0_removed=True,validation_opened=False))
    return dict(status='complete',phase1_generations=generations)

"""Receipt-bound observer diagnostics. Never modifies worker metrics or fitness."""
import json
from pathlib import Path
import torch
from .runtime import file_hash
from .position_tail import episode_pnls,tail_summary


def diagnostics(output,generation,candidates,*,full_training=False):
    """Read only requested lanes from a completed generation's durable receipts."""
    root=Path(output);record_path=(root/f'generation-{generation:04d}'/'complete.json') if full_training else (root/f'generation_{generation-1:03d}'/'generation.json')
    record=json.loads(record_path.read_text())
    if full_training and (record.get('status')!='complete' or record.get('validation_opened',True) or not record.get('selection_allowed') or len(set(record['training_days']))!=30):
        raise ValueError('Position diagnostics require complete all30 training receipts')
    binding=file_hash(record_path)
    samples={candidate:[] for candidate in candidates}
    entries=[dict(path=str(record_path.parent/day/'receipt.json'),sha256=record['session_receipts'][day]) for day in record['training_days']] if full_training else record['receipts']
    for entry in entries:
        path=Path(entry['path'])
        if full_training and path.resolve().parent.parent!=record_path.parent.resolve():raise ValueError('Observer session path escapes generation')
        if not path.resolve().is_relative_to(root.resolve()) or file_hash(path)!=entry['sha256']:
            raise ValueError('Observer session receipt path/hash mismatch')
        session=json.loads(path.read_text())
        if full_training and (session.get('validation_opened',True) or not session.get('full_session') or session.get('population_sha256')!=record['population_sha256']):raise ValueError('Observer session is not bound training data')
        for batch in session['batch_receipts']:
            receipt_path=path.parent/batch['directory']/'receipt.json'
            if receipt_path.resolve().parent.parent!=path.parent.resolve():raise ValueError('Observer batch path escapes session')
            if file_hash(receipt_path)!=batch['sha256']:raise ValueError('Observer batch receipt changed')
            receipt=json.loads(receipt_path.read_text())
            indices=receipt['candidate_indices'] if 'candidate_indices' in receipt else list(range(receipt['candidate_start'],receipt['candidate_start']+receipt['candidate_count']))
            requested=[(lane,index+1) for lane,index in enumerate(indices) if index+1 in samples]
            if not requested:continue
            fills=receipt_path.parent/'fills.pt'
            if file_hash(fills)!=receipt['ledger_sha256']:raise ValueError('Observer fill ledger changed')
            saved=torch.load(fills,map_location='cpu',weights_only=True)
            for lane,candidate in requested:
                values=episode_pnls(saved['ledger'][lane,:int(saved['counts'][lane])].numpy())
                if len(values)!=receipt['metrics']['closed_positions'][lane]:raise ValueError('Observer closed-position count mismatch')
                samples[candidate].extend(values.tolist())
    if file_hash(record_path)!=binding:raise ValueError('Observer generation changed during read')
    return {candidate:dict(**tail_summary(values),position_tail_receipt_sha256=binding) for candidate,values in samples.items()}

"""Offline V4 consumer: certified features and immutable broker snapshots."""
from pathlib import Path
import json
import torch
from .runtime import file_hash
from .tape import SqueezeTape
from .feature_bank import CertifiedBank

_EXECUTION_CACHE={}
_BANK_CACHE={}
_HOST_BYTES=0
HOST_BUDGET_BYTES=320*1024**3

def stamps(paths):return tuple((str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in paths)

def bank_cached(root,day=None):
    key=str(Path(root).resolve())
    if key not in _BANK_CACHE:
        value=CertifiedBank(root,expected_day=day)
        paths=[Path(root)/'plan.json',Path(root)/'complete.json',Path(root)/'bank'/'complete.json']+[Path(root)/'bank'/name for name in ('close_us.npy','scalar.npy','levels.npy')]
        _BANK_CACHE[key]=(value,paths,stamps(paths))
    value,paths,sealed=_BANK_CACHE[key]
    if stamps(paths)!=sealed:raise ValueError('Immutable feature input changed after verified load')
    if day and value.day['day']!=day:raise ValueError('Wrong cached feature day')
    return value

def load_execution(root):
    global _HOST_BYTES
    root=Path(root);receipt=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
    blob=root/'tape.pt'
    key=str(root.resolve());paths=(root/'receipt.json',blob)
    if key in _EXECUTION_CACHE:
        tape,binding,sealed=_EXECUTION_CACHE[key]
        if stamps(paths)!=sealed:raise ValueError('Immutable execution snapshot changed after verified load')
        return tape,binding
    if file_hash(blob)!=receipt['sha256']:raise ValueError('Execution snapshot bytes changed')
    tape=SqueezeTape(**torch.load(blob,map_location='cpu',weights_only=True)).validate()
    if tape.provenance['fingerprint']!=receipt['source_fingerprint'] or tape.bytes!=receipt['bytes']:
        raise ValueError('Execution snapshot source identity mismatch')
    binding=dict(receipt_sha256=file_hash(root/'receipt.json'),tape_sha256=receipt['sha256'],creator_identity=receipt['identity'],source_fingerprint=receipt['source_fingerprint'])
    if _HOST_BYTES+tape.bytes<=HOST_BUDGET_BYTES:
        _EXECUTION_CACHE[key]=(tape,binding,stamps(paths));_HOST_BYTES+=tape.bytes
    return tape,binding

def load_session(spec, *, isolated_banks=False):
    tape,receipt=load_execution(spec['execution_root'])
    # Split basis is session-specific mutable metadata. A prefetched prior
    # must not change the bank being consumed by the current GPU session.
    bank=bank_cached(spec['feature_root'],spec['day'])
    prior=bank_cached(spec['previous_feature_root']) if spec.get('previous_feature_root') else None
    if isolated_banks:
        from copy import copy
        bank=copy(bank)
        prior=copy(prior) if prior is not None else None
    if prior is not None and prior.day['day']>=spec['day']:raise ValueError('Future prior feature context')
    mapping_path=Path(spec['identity_map']);mapping=json.loads(mapping_path.read_text(encoding='utf-8'))
    if mapping['bank_certificate_sha256']!=bank.certificate_hash:raise ValueError('Identity mapping not bound to current bank')
    rows=mapping['listing_to_ticker']
    if set(rows)!=set(bank.manifest['offsets']) or len(set(rows.values()))!=len(rows):raise ValueError('Incomplete/ambiguous identity mapping')
    inverse={ticker:identity for identity,ticker in rows.items()}
    absent=set(tape.tickers)-set(inverse)
    if absent:raise ValueError(f'{len(absent)} execution tickers absent from V6-schema bank')
    identities=[inverse[t] for t in tape.tickers]
    # An empty action list is accepted only from a certified opening-as-of
    # reference query, never inferred from a missing sidecar.
    from .splits import load_basis
    bank.split_basis,split_hash=load_basis(spec['split_certificate'],bank,prior,rows)
    previous_split_hash=None
    if prior is not None:
        previous_certificate=json.loads(Path(spec['previous_split_certificate']).read_text(encoding='utf-8'))
        prior_mapping={key:value['ticker'] for key,value in previous_certificate['listings'].items()}
        if set(prior_mapping)!=set(prior.manifest['offsets']):raise ValueError('Incomplete previous split coverage')
        prior.split_basis,previous_split_hash=load_basis(spec['previous_split_certificate'],prior,None,prior_mapping,rvol_only=True)
    # No nearest-time matching or silent source substitution. Actual bank
    # closes must reconcile against observed execution marks in the session.
    import numpy as np
    clock=tape.clocks.numpy()*1_000_000
    for ticker,identity in enumerate(identities):
        a,b=bank.manifest['offsets'][identity];times=bank.clocks[a:b]
        position=np.searchsorted(times,clock);safe=np.minimum(position,max(0,len(times)-1))
        observed=tape.observed[:,ticker].numpy()
        if not len(times):
            if observed.any():raise ValueError('Observed execution listing has no bank candle')
            continue
        matched=(position<len(times))&(times[safe]==clock)
        if np.any(observed&~matched):raise ValueError('Observed broker candle missing from feature bank')
        selected=np.flatnonzero(observed&matched)
        valid=bank.scalar[a+safe[selected],35]==1
        prices=np.exp(bank.scalar[a+safe[selected],3].astype(np.float64))
        expected=tape.close[selected,ticker].numpy()
        if not valid.all() or not np.allclose(prices,expected,rtol=2e-6,atol=1e-4):raise ValueError('Feature/financial prices or validity disagree')
    return tape,bank,prior,identities,dict(day=spec['day'],execution=receipt,feature_certificate=bank.certificate_hash,prior_certificate=prior.certificate_hash if prior else None,identity_map_sha256=file_hash(mapping_path),split_certificate_sha256=split_hash,previous_split_certificate_sha256=previous_split_hash)

def preflight(spec,*,require_full=True,profile=False):
    training=spec.get('training',[]);validation=spec.get('validation',[])
    if require_full and (len(training)!=30 or len(validation)!=6):raise ValueError('Full V4 requires exactly 30 training and six validation sessions')
    train_dates=[s['day'] for s in training];test_dates=[s['day'] for s in validation]
    if len(set(train_dates+test_dates))!=len(train_dates+test_dates) or train_dates!=sorted(train_dates) or test_dates!=sorted(test_dates) or (test_dates and max(train_dates)>=min(test_dates)):
        raise ValueError('Chronological disjoint training/final-validation split required')
    missing=[]
    # Validation data is NOT opened; final schema/metric audits follow freeze.
    for s in training[:1] if profile else training:
        for key in ('execution_root','feature_root','identity_map','split_certificate'):
            if key not in s or not Path(s[key]).exists():missing.append(dict(day=s['day'],input=key))
        if s.get('previous_feature_root') and (not s.get('previous_split_certificate') or not Path(s['previous_split_certificate']).exists()):missing.append(dict(day=s['day'],input='previous_split_certificate'))
    if missing:raise ValueError('Missing certified V4 inputs: '+json.dumps(missing))
    return dict(training_dates=train_dates,validation_dates=test_dates,validation_opened=False,input_scope='first_training_session_profile' if profile else 'all_training_sessions')

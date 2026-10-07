"""Session-major bounded GPU candidate batches; shared immutable input residency."""
import gc
import time
import torch
from .gate_compiler import FeatureResident
from .program_runner import ProgramRunner
from .session_pool import padded_tape, bind_tape, can_bind_tape
from .run_search import clean, state, fingerprint, seal_ledger
from .runtime import write_json, file_hash, require_runtime
from .rule_prefetch import RulePrefetch,ReceiptWriter


def publish_batch(folder,path,record,ledger,counts,metadata):
    record['ledger_sha256']=seal_ledger(folder/'fills.pt',ledger,counts)
    write_json(path,record)
    metadata['sha256']=file_hash(path)


def merge_metrics(parts,order=None):
    keys = set(parts[0])
    if any(set(part) != keys for part in parts):
        raise ValueError('Candidate batch metric schema mismatch')
    merged={key: sum((part[key] for part in parts), []) for key in keys}
    if order is None:return merged
    if sorted(order)!=list(range(len(order))) or any(len(v)!=len(order) for v in merged.values()):
        raise ValueError('Execution permutation must cover every candidate exactly once')
    inverse=[0]*len(order)
    for execution,candidate in enumerate(order):inverse[candidate]=execution
    return {key:[values[i] for i in inverse] for key,values in merged.items()}


class BatchedEvaluator:
    """One GPU owner; Torch lanes execute in parallel, never competing processes."""
    def __init__(self, space, args):
        self.space, self.args, self.runner = space, args, None
        self.key = None

    def close(self):
        self.runner = None
        self.key = None
        gc.collect()
        if self.args.device == 'cuda':
            torch.cuda.empty_cache()

    def evaluate(self, spec, population, destination, prepared, emit):
        args = self.args
        began = time.perf_counter()
        (host_tape, bank, prior, identities, binding), load, wait = prepared
        destination = require_runtime(destination)
        pop_hash = fingerprint([state(v) for v in population])
        specialize=not getattr(args,'reference_execution',False)
        order=sorted(range(len(population)),key=lambda i:(int(population[i].policy[4]),i)) if specialize else list(range(len(population)))
        receipt_path = destination/'receipt.json'
        if receipt_path.exists():
            receipt = __import__('json').loads(receipt_path.read_text())
            if receipt['population_sha256'] != pop_hash or receipt['day'] != spec['day']:
                raise ValueError('Resumed population or day changed')
            if receipt.get('candidate_order',list(range(len(population))))!=order:
                raise ValueError('Resumed execution permutation changed')
            for key in ('execution', 'feature_certificate', 'prior_certificate', 'identity_map_sha256',
                        'split_certificate_sha256', 'previous_split_certificate_sha256'):
                if receipt[key] != binding[key]:
                    raise ValueError('Resumed immutable input changed')
            for batch in receipt['batch_receipts']:
                if file_hash(destination/batch['directory']/'receipt.json') != batch['sha256']:
                    raise ValueError('Resumed candidate batch receipt changed')
                recorded = __import__('json').loads((destination/batch['directory']/'receipt.json').read_text())
                if file_hash(destination/batch['directory']/'fills.pt') != recorded['ledger_sha256']:
                    raise ValueError('Resumed fill ledger changed')
            return receipt
        emit(stage='Transfer certified inputs', active_session=None)
        transfer_start = time.perf_counter()
        capacity = args.ticker_capacity or ((len(host_tape.tickers)+63)//64)*64
        if capacity < len(host_tape.tickers):
            raise ValueError('Candidate batching cannot truncate listings')
        estimate = host_tape.bytes*capacity/len(host_tape.tickers)
        if estimate > args.maximum_tape_gib*1024**3:
            raise MemoryError('Broker tape exceeds declared budget')
        if self.runner is not None and can_bind_tape(self.runner.tape,host_tape,capacity):
            tape=self.runner.tape
            bind_tape(tape,host_tape)
        else:
            tape = padded_tape(host_tape, capacity, args.device)
        if tape.bytes > args.maximum_tape_gib*1024**3:
            raise MemoryError('Padded tape exceeds declared budget')
        resident = FeatureResident(bank, identities, previous=prior, device=args.device,
                                   maximum_gib=args.feature_gib, start_us=int(tape.clocks[0])*1_000_000,
                                   end_us=int(tape.clocks[-1])*1_000_000)
        transfer = time.perf_counter()-transfer_start
        pieces, batches = [], []
        totals = dict(load=load, prefetch_wait=wait, transfer=transfer, rule_prepare=0., compile=0., replay=0.)
        totals.update(rule_prefetch_wait=0.,rule_overlap=0.,receipt_write_wait=0.)
        total_batches = (len(population)+args.batch_size-1)//args.batch_size
        overlapping=getattr(args,'rule_prefetch',False)
        async_writes=getattr(args,'concurrent_writes',False)
        emit(rule_prefetch_enabled=overlapping,rule_prefetch_backend=args.device,concurrent_writes_enabled=async_writes)
        replay_intervals={}
        with RulePrefetch(args.device,getattr(args,'rule_prefetch_gib',24.)) as prefetch, ReceiptWriter() as writer:
            for index, left in enumerate(range(0, len(population), args.batch_size)):
                batch_started = time.perf_counter()
                indices=order[left:left+args.batch_size]
                members = [population[i] for i in indices]
                folder = require_runtime(destination/f'batch_{index:04d}')
                path = folder/'receipt.json'
                member_hash = fingerprint([state(v) for v in members])
                if path.exists():
                    record = __import__('json').loads(path.read_text())
                    if (record['population_sha256'] != member_hash or record['session_binding_sha256'] != fingerprint(binding)
                            or record.get('candidate_indices',list(range(left,left+len(members))))!=indices
                            or file_hash(folder/'fills.pt') != record['ledger_sha256']):
                        raise ValueError('Partial candidate batch identity changed')
                    metadata=dict(directory=folder.name,sha256=file_hash(path))
                else:
                    emit(stage='Compile lifecycle rules', completed_batches=index, total_batches=total_batches,
                         candidate_start=left, candidate_end=left+len(members))
                    execution_key=ProgramRunner.specialization_key(members,self.space) if specialize else None
                    key = (len(tape.clocks), capacity, len(members), tuple(tape.level_lower.shape), tape.structural_targets is not None,execution_key)
                    emit(execution_lot_capacity=execution_key[0] if specialize else 15,
                         execution_specialization=execution_key)
                    reuse = self.runner is not None and self.key == key
                    old_gates=self.runner.program_gates if overlapping and self.runner is not None else None
                    reuse_gates=self.runner is not None and self.runner.program_gates.shape==(len(tape.clocks),len(members),capacity)
                    rule_wait=0.;overlap=0.
                    if prefetch.index==index:
                        (gates,rule_seconds),rule_wait,rule_begin,rule_end=prefetch.take(index)
                        previous_begin,previous_end=replay_intervals[index]
                        overlap=max(0.,min(previous_end,rule_end)-max(previous_begin,rule_begin))
                    else:
                        gates, rule_seconds = resident.compile(members, tape, chunk_candles=args.chunk_candles,
                                                           emit=lambda event: emit(**event),maximum_gate_gib=args.maximum_gate_gib,
                                                           out=self.runner.program_gates if reuse_gates else None,specialize_windows=specialize)
                        rule_wait=rule_seconds
                    compiled = 0.
                    if reuse:
                        if self.runner.tape is not tape:bind_tape(self.runner.tape, tape)
                        self.runner.start_boundary.fill_(int(tape.provenance.get('start_second', int(tape.clocks[0]))))
                        self.runner.end_boundary.copy_(tape.clocks[-1])
                        self.runner.set_population(members, gates)
                    else:
                        self.close()
                        self.runner = ProgramRunner(tape, self.space, members, gates, backend=args.backend,
                                                    specialize=specialize,
                                                    maximum_fills=args.maximum_fills, maximum_state_gib=args.maximum_state_gib,
                                                    graph_steps=args.graph_steps)
                        emit(stage='Compile financial replay')
                        self.runner.compile()
                        compiled = self.runner.setup_seconds
                        self.key = key
                    # Recycled storage is never the captured runner's active gates.
                    spare=gates if reuse and gates is not self.runner.program_gates else old_gates
                    if spare is self.runner.program_gates:spare=None
                    del gates
                    next_index=index+1;next_left=left+args.batch_size
                    if overlapping and next_index<total_batches and not (destination/f'batch_{next_index:04d}'/'receipt.json').exists():
                        next_members=[population[i] for i in order[next_left:next_left+args.batch_size]]
                        next_shape=(len(tape.clocks),len(next_members),capacity)
                        if spare is not None and tuple(spare.shape)!=next_shape:spare=None
                        def prepare(out,next_members=next_members):
                            return resident.compile(next_members,tape,chunk_candles=args.chunk_candles,
                                maximum_gate_gib=args.maximum_gate_gib,out=out,specialize_windows=specialize)
                        prefetch.submit(next_index,next_shape,prepare,spare)
                    del spare,old_gates
                    replay_started = time.perf_counter()
                    emit(stage='Backtest', replay_started_epoch=time.time())
                    def progress(cursor):
                        elapsed = time.perf_counter()-replay_started
                        done = cursor['completed_seconds']
                        total = getattr(args,'profile_seconds',None) or cursor['total_seconds']
                        cursor = dict(cursor,total_seconds=total)
                        emit(progress=cursor, replay_elapsed=elapsed, replay_rate=done/elapsed if elapsed else None,
                             replay_eta=(total-done)*elapsed/done if done else None,
                             rule_prefetch_batch=prefetch.index,rule_prefetch_ready=prefetch.future.done() if prefetch.future else False,
                             receipt_writer_pending=writer.future is not None,
                             active_session=self.runner.live_metrics())
                    steps=getattr(args,'profile_seconds',None)
                    if steps==len(tape.clocks):steps=None
                    result = self.runner.run(progress=progress,steps=steps)
                    if prefetch.index is not None:replay_intervals[prefetch.index]=(replay_started,time.perf_counter())
                    metrics = {k: clean(v) for k, v in result.items() if isinstance(v, torch.Tensor) or k == 'closed_position_duration_samples'}
                    ledger = self.runner.ledger[:, :int(self.runner.fill_count.max())].detach().cpu()
                    if ledger.device==self.runner.ledger.device:ledger=ledger.clone()
                    counts=self.runner.fill_count.detach().cpu().clone()
                    record = dict(population_sha256=member_hash, session_binding_sha256=fingerprint(binding),
                                  candidate_start=left, candidate_count=len(members),candidate_indices=indices, metrics=metrics,
                                  execution_specialization=execution_key,
                                  timing=dict(rule_prepare=rule_seconds, compile=compiled, replay=result['replay_seconds'],
                                              rule_prefetch_wait=rule_wait,rule_overlap=overlap))
                    metadata=dict(directory=folder.name)
                    if async_writes:
                        writer.submit(lambda folder=folder,path=path,record=record,ledger=ledger,counts=counts,metadata=metadata:
                                      publish_batch(folder,path,record,ledger,counts,metadata))
                    else:publish_batch(folder,path,record,ledger,counts,metadata)
                pieces.append(record['metrics'])
                for key, value in record['timing'].items():
                    totals[key] += value
                batches.append(metadata)
                del metadata
                elapsed=time.perf_counter()-batch_started
                emit(completed_batches=index+1, total_batches=total_batches,
                     average_batch_seconds=(time.perf_counter()-began-transfer-wait)/(index+1),
                     session_eta=(total_batches-index-1)*elapsed)
                if (self.args.output/'STOP').exists():
                    writer.drain()
                    raise InterruptedError('Stopped at durable candidate-batch boundary')
            writer.drain()
            totals['receipt_write_wait']=writer.wait_seconds
            totals['end_to_end'] = time.perf_counter()-began+wait
            receipt = dict(binding, population_sha256=pop_hash, batch_size=args.batch_size,
                           batch_receipts=batches,candidate_order=order, metrics=merge_metrics(pieces,order), timing=totals,
                           profile_seconds=getattr(args,'profile_seconds',None))
            write_json(receipt_path, receipt)
        del resident, tape
        return receipt

"""Independent V4 financial runner with device-resident lifecycle rule gates."""
import torch
from .search_runner import SearchRunner
from .evolution import STAGES

class ProgramRunner(SearchRunner):
    def __init__(self,tape,space,individuals,gates,**kwargs):
        self.native_programs=True
        self.program_gates=gates
        shape=(len(tape.clocks),len(individuals),len(tape.tickers))
        valid=(gates.shape==shape and gates.dtype==torch.uint8 and gates.device==tape.device) if isinstance(gates,torch.Tensor) else (set(gates)==set(STAGES) and all(v.shape==shape and v.dtype==torch.bool and v.device==tape.device for v in gates.values()))
        if not valid:
            raise ValueError('Require all lifecycle gates on the tape device')
        super().__init__(tape,space,[v.policy for v in individuals],**kwargs)

    def _program_gate(self,stage):
        if isinstance(self.program_gates,torch.Tensor):
            return self.program_gates.index_select(0,self.index.reshape(1)).squeeze(0).bitwise_and(1<<STAGES.index(stage))!=0
        return self.program_gates[stage].index_select(0,self.index.reshape(1)).squeeze(0)

    def _entry_filter(self,now,close):return self._program_gate('entry')

    def set_population(self,individuals,gates):
        self.set_genomes([v.policy for v in individuals])
        if isinstance(self.program_gates,torch.Tensor):
            if not isinstance(gates,torch.Tensor) or gates.shape!=self.program_gates.shape:raise ValueError('Packed gate allocation changed')
            self.program_gates.copy_(gates);return
        for stage in STAGES:
            if gates[stage].shape!=self.program_gates[stage].shape:raise ValueError('Program gate allocation changed')
            self.program_gates[stage].copy_(gates[stage])

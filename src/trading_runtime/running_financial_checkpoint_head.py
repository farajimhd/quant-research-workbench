"""Managed latest running checkpoint receipt, never historical cash authority."""
from __future__ import annotations
from dataclasses import dataclass
import re
from uuid import UUID
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.keeper_ownership import _identity, _path

@dataclass(frozen=True, slots=True)
class RunningFinancialCheckpointHead:
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str
    keeper_version: int

    def __post_init__(self):
        _identity(self.run_id,'run')
        if (type(self.run_id) is not str or type(self.checkpoint_sequence) is not int
                or not 0 < self.checkpoint_sequence < 2**64
                or type(self.keeper_version) is not int or self.keeper_version < 0
                or type(self.journal_batch_id) is not str
                or str(UUID(self.journal_batch_id)) != self.journal_batch_id
                or UUID(self.journal_batch_id).int == 0
                or type(self.snapshot_hash) is not str
                or re.fullmatch('[0-9a-f]{64}',self.snapshot_hash) is None
                or self.snapshot_hash == '0'*64):
            raise ValueError('Running financial checkpoint head is malformed')

class ManagedRunningFinancialCheckpointHeadReader:
    """Read only the latest Keeper-selected receipt; orphan rows are not selected."""
    def __init__(self,session):
        if not isinstance(session,ManagedKeeperSession):
            raise TypeError('Running checkpoint head requires managed Keeper')
        self._session=session

    @staticmethod
    def path(run_id):
        _identity(run_id,'run')
        return _path('typed_dispatch_running_financial_head',run_id)

    def read_head(self,*,run_id):
        session=self._session; client=session.client
        if not session.writable or client.client_id is None:
            raise RuntimeError('Running checkpoint Keeper session is unavailable')
        generation,client_id=session._generation,client.client_id
        try:
            raw,stat=client.get(self.path(run_id))
            fields=raw.decode('utf-8').split('\n')
            if (len(fields)!=5 or fields[:2]!=['1',run_id]
                    or str(int(fields[2]))!=fields[2]):
                raise ValueError('Noncanonical head wire')
            result=RunningFinancialCheckpointHead(run_id,int(fields[2]),fields[3],fields[4],stat.version)
        except Exception as exc:
            if (not session.writable or session._generation!=generation
                    or client.client_id!=client_id):
                raise RuntimeError('Running checkpoint Keeper session changed during read') from exc
            raise ValueError('Running checkpoint Keeper head missing or corrupt') from exc
        if (not session.writable or session._generation!=generation
                or client.client_id!=client_id):
            raise RuntimeError('Running checkpoint Keeper session changed during read')
        return result

    def read_optional_head(self,*,run_id):
        """Only an exact absent Keeper node returns None; corruption never does."""
        try:
            return self.read_head(run_id=run_id)
        except ValueError as exc:
            if type(exc.__cause__).__name__ == 'NoNodeError':
                return None
            raise

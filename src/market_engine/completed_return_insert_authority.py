"""Product-specific Keeper ownership and durable unknown-INSERT fencing.

No TTL or later table SELECT can resolve an unknown dispatched HTTP request.
Operator bootstrap supplies the connected, ACL-scoped Keeper client. A separate
trusted terminal-dispatch verifier is required for explicit recovery; none is
installed by this module. No financial journal authority is used.
"""
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import uuid4

from src.market_engine.completed_endpoint_return_contract import require_hash, require_uuid

ROOT = '/market-products/completed-endpoint-return/insert-authority/v2'


class InsertAuthorityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TerminalInsertResolution:
    query_id: str
    payload_hash: str
    terminal_evidence_hash: str

    def __post_init__(self):
        require_uuid(self.query_id)
        require_hash(self.payload_hash)
        require_hash(self.terminal_evidence_hash)


class TerminalInsertResolver(ABC):
    """Trusted authority proving the original dispatch can NEVER append later.

    A caller boolean, quiet process snapshot, elapsed timeout, or child-row
    SELECT is insufficient. Production wiring must supply durable original
    terminal query/dispatch identity and exact endpoint/producer principal proof.
    No general query-log fallback or concrete production resolver exists here.
    """
    @abstractmethod
    def verify_original_terminal(self, attempt, query_id, payload_hash):
        """Return TerminalInsertResolution or raise; no transport retry."""


class KeeperProductInsertAuthority:
    def __init__(self, keeper):
        self.keeper = keeper

    @property
    def namespace(self):
        return ROOT

    @property
    def allowed_tables(self):
        from src.market_engine.completed_return_campaign_contract import CERTIFICATE_TABLE
        from src.market_engine.completed_endpoint_return_contract import FEATURE_TABLE, COVERAGE_TABLE
        return (FEATURE_TABLE,COVERAGE_TABLE,CERTIFICATE_TABLE)

    def _connected(self):
        if getattr(self.keeper, 'connected', False) is not True:
            raise InsertAuthorityUnavailable('Producer Keeper connection unavailable')

    @contextmanager
    def _ownership(self, attempt, *, resolution=False):
        require_uuid(attempt)
        self._connected()
        path = self.namespace + '/' + attempt
        self.keeper.ensure_path(path)
        owner = str(uuid4()).encode()
        try:
            self.keeper.create(path + '/owner', owner, ephemeral=True)
        except Exception as exc:
            raise InsertAuthorityUnavailable('Producer attempt already owned; no concurrent writer') from exc
        lease = ProductInsertLease(self, path, owner)
        try:
            try:
                self.keeper.create(path + '/gate', _wire(dict(state='fresh')))
            except Exception:
                # A create race/error is never interpreted as absence. Existing
                # exact durable gate must be readable and valid before use.
                lease.read()
            gate, _ = lease.read()
            if gate['state'] in ('pending', 'ack') and not resolution:
                raise InsertAuthorityUnavailable('Prior INSERT unknown/unverified; explicit authoritative resolution required')
            yield lease
        finally:
            # Releasing ephemeral ownership never clears persistent dispatch.
            try:
                current, stat = self.keeper.get(path + '/owner')
                if current == owner:
                    self.keeper.delete(path + '/owner', version=stat.version)
            except Exception:
                pass

    def ownership(self, attempt):
        return self._ownership(attempt)

    def resolve_original_terminal(self, attempt, resolver):
        if not isinstance(resolver, TerminalInsertResolver):
            raise InsertAuthorityUnavailable('Recovery requires trusted typed original-terminal resolver')
        with self._ownership(attempt, resolution=True) as lease:
            gate, version = lease.read()
            if gate['state'] not in ('pending', 'ack'):
                raise InsertAuthorityUnavailable('Attempt has no unresolved original dispatch')
            proof = resolver.verify_original_terminal(attempt, gate['query_id'], gate['payload_hash'])
            if type(proof) is not TerminalInsertResolution:
                raise InsertAuthorityUnavailable('Terminal resolver did not issue typed evidence')
            proof.__post_init__()
            if proof.query_id != gate['query_id'] or proof.payload_hash != gate['payload_hash']:
                raise InsertAuthorityUnavailable('Terminal evidence differs from original dispatch')
            lease.retain_receipt('terminal-resolutions', proof.query_id,
                dict(query_id=proof.query_id, payload_hash=proof.payload_hash,
                     terminal_evidence_hash=proof.terminal_evidence_hash))
            lease.set(dict(state='idle', terminal_resolution=proof.terminal_evidence_hash), version)

    def assert_complete(self, attempt, projection_token):
        self._connected()
        try:
            wire, _ = self.keeper.get(self.namespace + '/' + attempt + '/gate')
            gate = _decode(wire,self.allowed_tables)
        except Exception as exc:
            raise InsertAuthorityUnavailable('Installed product lacks producer completion fence') from exc
        if gate != dict(state='complete', projection_token=projection_token):
            raise InsertAuthorityUnavailable('Installed product has unresolved/incomplete producer dispatch')


def _wire(gate):
    return json.dumps(gate, sort_keys=True, separators=(',', ':')).encode('ascii')


def _decode(wire,allowed_tables):
    try:
        gate = json.loads(wire)
        state = gate['state']
        expected = {'fresh': {'state'}, 'idle': {'state'},
            'pending': {'state','table','query_id','payload_hash'},
            'ack': {'state','table','query_id','payload_hash'},
            'complete': {'state','projection_token'}}
        if state == 'idle' and 'terminal_resolution' in gate:
            require_hash(gate['terminal_resolution'])
            expected['idle'] = {'state','terminal_resolution'}
        if set(gate) != expected[state] or _wire(gate) != wire:
            raise ValueError('noncanonical gate')
        if state in ('pending','ack'):
            if gate['table'] not in allowed_tables:
                raise ValueError('foreign table')
            require_uuid(gate['query_id'])
            require_hash(gate['payload_hash'])
        if state == 'complete': require_hash(gate['projection_token'])
        return gate
    except Exception as exc:
        raise InsertAuthorityUnavailable('Producer INSERT gate corrupt') from exc


class ProductInsertLease:
    def __init__(self, authority, path, owner):
        self.authority, self.path, self.owner = authority, path, owner

    def assert_owner(self):
        self.authority._connected()
        try:
            wire, _ = self.authority.keeper.get(self.path + '/owner')
        except Exception as exc:
            raise InsertAuthorityUnavailable('Producer ownership lost or Keeper unavailable') from exc
        if wire != self.owner:
            raise InsertAuthorityUnavailable('Producer ownership lost; no further dispatch')

    def read(self):
        self.assert_owner()
        wire, stat = self.authority.keeper.get(self.path + '/gate')
        return _decode(wire,self.authority.allowed_tables), stat.version

    def set(self, gate, version):
        self.assert_owner()
        # Versioned Keeper CAS. If response is uncertain, subsequent read must
        # fail/verify exact state; never retry a dispatched HTTP insertion.
        self.authority.keeper.set(self.path + '/gate', _wire(gate), version=version)

    def retain_receipt(self, kind, query_id, fields):
        self.assert_owner()
        require_uuid(query_id)
        if kind not in ('verified-inserts', 'terminal-resolutions'):
            raise InsertAuthorityUnavailable('Foreign producer dispatch receipt kind')
        parent = self.path + '/' + kind
        self.authority.keeper.ensure_path(parent)
        path, wire = parent + '/' + query_id, _wire(fields)
        try:
            self.authority.keeper.create(path, wire)
        except Exception:
            existing, _ = self.authority.keeper.get(path)
            if existing != wire:
                raise InsertAuthorityUnavailable('Immutable producer dispatch receipt differs')

    def admit_existing(self, has_rows):
        gate, version = self.read()
        if gate['state'] == 'fresh':
            if has_rows:
                raise InsertAuthorityUnavailable('Unregistered prior rows cannot be adopted into new authority')
            self.set(dict(state='idle'), version)

    def execute(self, client, table, payload, verify_readback):
        if table not in self.authority.allowed_tables:
            raise InsertAuthorityUnavailable('Foreign product table; dispatch rejected')
        gate, version = self.read()
        if gate['state'] != 'idle':
            raise InsertAuthorityUnavailable('INSERT requires free durable producer dispatch gate')
        pending = dict(state='pending', table=table, query_id=str(uuid4()), payload_hash=sha256(payload).hexdigest())
        self.set(pending, version)
        self.assert_owner()
        # Synchronous POST, exact bytes, unique query identity, no retry. Any
        # exception leaves pending even if a later SELECT shows all rows.
        client.execute(payload, query_id=pending['query_id'])
        current, version = self.read()
        if current != pending:
            raise InsertAuthorityUnavailable('Original INSERT acknowledgement lost dispatch fence')
        ack = dict(pending, state='ack')
        self.set(ack, version)
        verify_readback()
        current, version = self.read()
        if current != ack:
            raise InsertAuthorityUnavailable('Verified INSERT lost acknowledged fence')
        self.retain_receipt('verified-inserts', ack['query_id'], ack)
        self.set(dict(state='idle'), version)

    def complete(self, projection_token):
        gate, version = self.read()
        expected = dict(state='complete', projection_token=projection_token)
        if gate == expected: return
        if gate['state'] != 'idle':
            raise InsertAuthorityUnavailable('Cannot certify pending/unverified producer INSERT')
        self.set(expected, version)

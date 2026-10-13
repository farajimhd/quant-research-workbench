"""SELECT-only proof of absent compatible published predecessor receipts."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re

from pipelines.market_sip.events.market_day_checkpoint import _scope
from pipelines.market_sip.events.market_day_sql import literal
from src.trading_runtime.arte_market_day_publisher import _placement
from src.trading_runtime.journal_contract import canonical_json

_PROOF = object()


@dataclass(frozen=True)
class EmptyMarketDayPredecessors:
    build_id: str
    scopes: tuple
    profile: tuple
    _hash: str = field(repr=False)
    _proof: object = field(repr=False)

    def verify(self):
        expected = sha256(canonical_json([self.build_id, self.scopes, self.profile]).encode()).hexdigest()
        if self._proof is not _PROOF or self._hash != expected:
            raise ValueError('Empty predecessor declaration lacks unchanged native read evidence')


async def certify_empty_market_day_predecessors(reader_factory, build_id, scopes, *, profile):
    """Fail on any compatible published technical receipt, including bad ones.

    A conflicting/invalid published parent must be resolved by native parent
    audit; it must never be reinterpreted as an absent parent/bootstrap seed.
    """
    from src.backend.qmd_columnar_history_transport import _owned_worker
    scopes = _scope(build_id, scopes)
    if (not callable(reader_factory) or type(profile) is not tuple or len(profile) != 3
            or any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None for value in profile[:2])
            or type(profile[2]) is not str or not profile[2]):
        raise ValueError('Empty predecessor proof requires an explicit source profile and reader')
    def read():
        reader = reader_factory()
        try:
            if reader.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Empty predecessor audit requires SELECT-only transport')
            _placement(reader)
            for first in range(0, len(scopes), 256):
                packet = scopes[first:first + 256]
                keys = ','.join(f'(toDate({literal(day)}),{literal(ticker)})' for day, ticker in packet)
                text = reader.execute("SELECT DISTINCT s.session_date,s.ticker "
                    "FROM arte.market_day_stage_certificate_v1 AS s "
                    "INNER JOIN arte.market_day_build_header_v1 AS h ON s.build_id=h.build_id "
                    "INNER JOIN arte.market_day_build_fence_v1 AS f ON s.build_id=f.build_id "
                    f"WHERE (s.session_date,s.ticker) IN ({keys}) AND s.stage='technical' "
                    f"AND h.calculation_source_hash={literal(profile[0])} "
                    f"AND h.rules_hash={literal(profile[1])} AND h.version={literal(profile[2])} "
                    f"LIMIT {len(packet) + 1} FORMAT JSONEachRow")
                if not isinstance(text, str) or len(text.encode()) > 8 * 1024 * 1024:
                    raise ValueError('Empty predecessor response exceeds its byte bound')
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
                if rows:
                    raise ValueError('Declared empty predecessor has compatible published receipts')
            digest = sha256(canonical_json([build_id, scopes, profile]).encode()).hexdigest()
            return EmptyMarketDayPredecessors(build_id, scopes, profile, digest, _PROOF)
        finally:
            reader.close()
    return await _owned_worker(read)


async def reverify_empty_market_day_predecessors(proof, reader_factory):
    if type(proof) is not EmptyMarketDayPredecessors:
        raise TypeError('Predecessor absence recheck needs native read evidence')
    proof.verify()
    return await certify_empty_market_day_predecessors(reader_factory, proof.build_id,
        proof.scopes, profile=proof.profile)

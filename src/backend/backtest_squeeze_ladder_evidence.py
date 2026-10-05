"""Prepared normalized ladder evidence; not installed or writer-admitted.

No BOS fiction, generic payload column or runtime sidecar is an authority.
These scalar rows need independent source readback and a V4 family fence before
the eventual numbered strategy can publish or submit a financial command.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from struct import pack, unpack
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from src.backend.backtest_squeeze_ladder_admission import LadderAdmissionDecision
from src.trading_runtime.arte_squeeze_ladder_schema import SETUP, TARGET, TABLES
from src.trading_runtime.journal_contract import canonical_json


@dataclass(frozen=True, slots=True)
class LadderEvidenceRows:
    setup: dict
    targets: tuple[dict, ...]


def _bits(value):
    return unpack('<Q', pack('<d', float(value)))[0]


def project_ladder_evidence(admission: LadderAdmissionDecision, *, run_id: str,
                            batch_id: str, parent_record_id: str,
                            assignment_id: str, session_date: date) -> LadderEvidenceRows:
    """Represent one admitted intent's evidence without granting durability.

    Projection hashes detect alterations; they do not certify market authority.
    Writer admission must independently reconstruct this result from pinned
    source plans and verify the exact intent/event parent and session clock.
    """
    if (not isinstance(admission, LadderAdmissionDecision) or admission.reason != 'capital_request_proposed'
            or admission.intent is None or not run_id or not assignment_id or type(session_date) is not date):
        raise ValueError('Ladder evidence requires a complete prepared admission')
    for identity in (batch_id, parent_record_id):
        if str(UUID(identity)) != identity or not UUID(identity).int:
            raise ValueError('Ladder evidence requires canonical nonzero UUID parents')
    decision, intent = admission.market_decision, admission.intent
    setup, resistance, stop = decision.setup, decision.setup.resistance, decision.setup.stop
    if (decision.reason != 'entry_proposed' or resistance is None or stop is None
            or decision.protection != intent.protection_profile
            or len(decision.target_level_ids) != len(decision.protection.slices)):
        raise ValueError('Ladder evidence differs from its admitted protection')
    expected_at = (datetime.combine(session_date, time(4), tzinfo=ZoneInfo('America/New_York'))
                   + timedelta(milliseconds=decision.boundary_ms)).astimezone(timezone.utc)
    if intent.event_time != expected_at:
        raise ValueError('Ladder evidence session differs from admitted intent clock')
    keys = dict(parent_record_id=parent_record_id, run_id=run_id,
                event_month=intent.event_time.date().replace(day=1).isoformat(), batch_id=batch_id)
    parent = dict(**keys, record_id=str(uuid5(NAMESPACE_URL, parent_record_id + ':ladder-setup')),
        ticker=setup.ticker, assignment_id=assignment_id, session_date=session_date.isoformat(),
        qualification_mode=setup.qualification_mode,
        admission_boundary_ms=setup.admission_boundary_ms, qualification_boundary_ms=setup.qualification_boundary_ms,
        boundary_ms=decision.boundary_ms, market_plan_token=setup.market_plan_token,
        scan_content_hash=setup.scan_content_hash, v7_plan_token=setup.v7_plan_token, pivot_plan_token=setup.pivot_plan_token,
        resistance_id=resistance.level_id, resistance_lower_bits=_bits(resistance.lower),
        resistance_upper_bits=_bits(resistance.upper), resistance_confirmed_epoch_ms=resistance.confirmed_at_ms,
        qualified_vwap_bits=resistance.vwap_bits, resistance_comparison_int=resistance.upper_comparison_int,
        pivot_id=stop.pivot.pivot_id, pivot_boundary_ms=stop.pivot.pivot_boundary_ms,
        pivot_confirmed_boundary_ms=stop.pivot.confirmed_boundary_ms, pivot_price_int=stop.pivot.price_int,
        stop_int=stop.stop_int, stop_buffer_int=stop.buffer_int, previous_close_int=decision.previous_close_int,
        close_int=decision.close_int, entry_limit_int=decision.entry_limit_int,
        target_count=len(decision.target_level_ids))
    parent['content_hash'] = sha256(canonical_json(parent).encode()).hexdigest()
    targets = []
    for ordinal, (identity, leg) in enumerate(zip(decision.target_level_ids, decision.protection.slices, strict=True)):
        row = dict(**keys, record_id=str(uuid5(NAMESPACE_URL, f'{parent_record_id}:ladder-target:{ordinal}')),
                   ordinal=ordinal, slice_id=leg.slice_id, level_id=identity,
                   price_bits=_bits(leg.profit_target_price), quantity_fraction_bits=_bits(leg.quantity_fraction))
        row['content_hash'] = sha256(canonical_json(row).encode()).hexdigest()
        targets.append(row)
    return LadderEvidenceRows(parent, tuple(targets))


def verify_ladder_evidence_projection(rows: LadderEvidenceRows, *, expected: LadderEvidenceRows) -> None:
    """Compare complete rows against independently reconstructed expectations.

    A valid stored hash alone grants nothing. This check is a prepared projection
    boundary; native writer/readback must provide source-certified expectations.
    """
    if not isinstance(rows, LadderEvidenceRows) or not isinstance(expected, LadderEvidenceRows):
        raise ValueError('Ladder evidence requires complete typed row families')
    if len(rows.targets) != len(expected.targets) or len(rows.targets) != rows.setup.get('target_count'):
        raise ValueError('Ladder evidence target family is incomplete')
    for contract, actual, desired in ((SETUP, rows.setup, expected.setup),
                                      *((TARGET, actual, desired) for actual, desired in zip(rows.targets, expected.targets, strict=True))):
        if set(actual) != {name for name, _ in contract.columns}:
            raise ValueError('Ladder evidence scalar columns differ')
        content = {name: value for name, value in actual.items() if name != 'content_hash'}
        if sha256(canonical_json(content).encode()).hexdigest() != actual['content_hash'] or actual != desired:
            raise ValueError('Ladder evidence differs from reconstructed source/parent')

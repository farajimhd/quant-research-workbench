"""SELECT-only immutable Strategy 43 configuration; no inherited parent."""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID
import re

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import NODE_TABLE, RELEASE_TABLE, encode_nodes, decode_nodes, node_hash
from src.trading_runtime.strategy_forty_three_release import configuration, verify_manifest
from src.trading_runtime.strategy_forty_three_rules import STRATEGY_ID, SOURCE_CANDIDATE_ID


def validate_definition_sources(definition):
    """Only the complete qualified session may enter this comparison release."""
    from datetime import date, time
    from src.trading_runtime.runtime import RunMode
    verify_manifest(definition.configuration_revision.get("payload", {}).get("strategy"))
    if (definition.mode != RunMode.BACKTEST or definition.session_date != date(2026, 9, 3)
            or definition.final_session_date not in (None, date(2026, 9, 3))
            or definition.start_time != time(4) or definition.end_time != time(9, 30)
            or definition.initial_cash != 10_000 or definition.tickers
            or definition.execution_interval != "100ms"
            or any(re.fullmatch(r"[0-9a-f]{64}", str(definition.market_data_plan.get(name) or "")) is None
                   for name in ("strategy_forty_three_history_token", "strategy_forty_three_identity_token",
                                "strategy_forty_three_structure_token", "strategy_forty_three_price_token"))):
        raise ValueError("Strategy 43 comparison requires certified full September 3 premarket, $10,000 and 100ms execution")


def compile_configuration(*, approved_code_commit, approved_code_fingerprint, approval_reference):
    payload = configuration(approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)
    nodes = encode_nodes(payload)
    return dict(source_candidate_id="torch-v2:" + SOURCE_CANDIDATE_ID,
        source_candidate_hash=SOURCE_CANDIDATE_ID, payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)


def verify_envelope(envelope):
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
        raise ValueError("Strategy 43 release envelope is absent")
    manifest = verify_manifest(envelope["payload"].get("strategy"))
    expected = compile_configuration(approved_code_commit=manifest["approved_code_commit"],
        approved_code_fingerprint=manifest["approved_code_fingerprint"],
        approval_reference=manifest["approval_reference"])
    if canonical_json(envelope) != canonical_json(expected):
        raise ValueError("Strategy 43 configuration includes foreign policy or changed source seals")
    return expected["payload"], encode_nodes(expected["payload"])


@dataclass(frozen=True, slots=True)
class CertifiedFortyThreeConfiguration:
    attempt_id: str
    payload_hash: str
    node_hash: str
    token: str
    payload: dict

    @property
    def source_candidate_hash(self):
        return SOURCE_CANDIDATE_ID

    @property
    def strategy_number(self):
        return 43

    def revision(self):
        run_plan = self.payload["run_plan"]["run_plan_id"]
        return dict(revision_id="strategy-one-43:" + self.attempt_id, revision=43, label="Strategy 43",
            release_state="test_candidate", content_hash=self.payload_hash, run_plan_id=run_plan,
            available_run_plans=[dict(run_plan_id=run_plan, name="Strategy 43", strategy_id=STRATEGY_ID,
                strategy_revision=43, profile_id="squeeze-grid-43")], payload=self.payload)


def certify_configuration(reader):
    def rows(query):
        return [json.loads(line) for line in reader.execute(query + " FORMAT JSONEachRow").splitlines() if line.strip()]
    releases = rows(f"SELECT release_attempt_id,strategy_id,source_candidate_id,source_candidate_hash,"
        f"payload_hash,node_count,node_hash FROM {RELEASE_TABLE} WHERE strategy_number=43")
    if len(releases) != 1 or releases[0]["strategy_id"] != STRATEGY_ID:
        raise RuntimeError("Strategy 43 requires exactly one independent immutable release")
    release = releases[0]
    attempt = str(UUID(release["release_attempt_id"]))
    children = rows("SELECT node_id,parent_node_id,child_key,child_ordinal,value_kind,text_value,"
        f"int_value,float_value,bool_value FROM {NODE_TABLE} WHERE strategy_number=43 "
        f"AND release_attempt_id=toUUID('{attempt}') ORDER BY node_id")
    envelope = {key: release[key] for key in ("source_candidate_id", "source_candidate_hash", "payload_hash", "node_count", "node_hash")}
    envelope["payload"] = decode_nodes(children)
    verify_envelope(envelope)
    if len(children) != release["node_count"] or node_hash(children) != release["node_hash"]:
        raise RuntimeError("Strategy 43 normalized configuration children differ")
    token = sha256(canonical_json(dict(attempt=attempt, **{k: v for k, v in release.items()
        if k != "release_attempt_id"})).encode()).hexdigest()
    return CertifiedFortyThreeConfiguration(attempt, release["payload_hash"], release["node_hash"], token, envelope["payload"])

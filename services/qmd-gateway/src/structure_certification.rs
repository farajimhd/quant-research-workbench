use crate::compact_event::LiveCompactEvent;
use crate::generic_structure::GenericStructureCheckpoint;
use chrono::{DateTime, NaiveDate, Utc};
use ring::digest::{Context, SHA256};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const STRUCTURE_CERTIFICATION_SCHEMA_VERSION: u16 = 2;
pub const STRUCTURE_CERTIFICATION_REPLAY_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct StructureCheckpointRecoveryAttestation {
    pub recovery_revision: String,
    pub source_checkpoint_set_id: String,
    pub source_checkpoint_sha256: String,
    pub source_chain_sha256: String,
    #[serde(default)]
    pub source_policy_revision: String,
    #[serde(default)]
    pub execution_clock_revision: String,
    pub delayed_trade_report_count: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct StructureReplayEvidence {
    pub event_count: u64,
    pub first_arrival_sequence: u64,
    pub last_arrival_sequence: u64,
    pub first_sip_timestamp_us: u64,
    pub last_sip_timestamp_us: u64,
    pub ordinal_contiguous: Option<bool>,
    pub event_sha256: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct StructureCheckpointCertification {
    pub schema_version: u16,
    pub event_evidence: StructureReplayEvidence,
    pub split_sha256: String,
    pub checkpoint_sha256: String,
    pub predecessor_checkpoint_sha256: String,
    pub predecessor_chain_sha256: String,
    pub chain_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery_attestation: Option<StructureCheckpointRecoveryAttestation>,
}

pub struct StructureEventAuditor {
    hasher: Context,
    event_bytes: Vec<u8>,
    event_count: u64,
    first_arrival_sequence: u64,
    last_arrival_sequence: u64,
    first_sip_timestamp_us: u64,
    last_sip_timestamp_us: u64,
    prior_order_key: Option<(u64, u64)>,
    require_contiguous_ordinals: bool,
    ordinal_contiguous: bool,
}

impl StructureEventAuditor {
    pub fn new(require_contiguous_ordinals: bool) -> Self {
        let mut hasher = Context::new(&SHA256);
        hasher.update(b"qmd-structure-event-stream-v1\0");
        Self {
            hasher,
            event_bytes: Vec::with_capacity(128),
            event_count: 0,
            first_arrival_sequence: 0,
            last_arrival_sequence: 0,
            first_sip_timestamp_us: 0,
            last_sip_timestamp_us: 0,
            prior_order_key: None,
            require_contiguous_ordinals,
            ordinal_contiguous: true,
        }
    }

    pub fn observe(&mut self, event: &LiveCompactEvent) -> Result<(), String> {
        let order_key = (event.sip_timestamp_us, event.arrival_sequence);
        if self.prior_order_key.is_some_and(|prior| order_key <= prior) {
            return Err(
                "structure certification event stream is not strictly causally ordered".to_string(),
            );
        }
        if self.event_count == 0 {
            self.first_arrival_sequence = event.arrival_sequence;
            self.first_sip_timestamp_us = event.sip_timestamp_us;
        } else if event.arrival_sequence != self.last_arrival_sequence.saturating_add(1) {
            self.ordinal_contiguous = false;
            if self.require_contiguous_ordinals {
                return Err(format!(
                    "structure certification ordinal discontinuity: expected {}, received {}",
                    self.last_arrival_sequence.saturating_add(1),
                    event.arrival_sequence,
                ));
            }
        }
        self.prior_order_key = Some(order_key);
        self.last_arrival_sequence = event.arrival_sequence;
        self.last_sip_timestamp_us = event.sip_timestamp_us;
        self.event_count = self.event_count.saturating_add(1);
        hash_compact_event(&mut self.hasher, &mut self.event_bytes, event);
        Ok(())
    }

    pub fn finish(self) -> StructureReplayEvidence {
        StructureReplayEvidence {
            event_count: self.event_count,
            first_arrival_sequence: self.first_arrival_sequence,
            last_arrival_sequence: self.last_arrival_sequence,
            first_sip_timestamp_us: self.first_sip_timestamp_us,
            last_sip_timestamp_us: self.last_sip_timestamp_us,
            ordinal_contiguous: self
                .require_contiguous_ordinals
                .then_some(self.ordinal_contiguous),
            event_sha256: format!("sha256:{}", hex(self.hasher.finish().as_ref())),
        }
    }
}

pub fn checkpoint_sha256(checkpoint: &GenericStructureCheckpoint) -> Result<String, String> {
    // Certification owns causal state. Presentation/selection projections are
    // recomputed from raw counts on load and must not strand a valid v16 chain
    // when their schema evolves.
    let value = checkpoint_json_value(checkpoint)?;
    canonical_json_sha256(&checkpoint_certification_value(&value))
}

pub fn checkpoint_certification_value(value: &Value) -> Value {
    let mut value = value.clone();
    remove_recomputable_hold_projections(&mut value);
    value
}

fn remove_recomputable_hold_projections(value: &mut Value) {
    match value {
        Value::Array(values) => {
            for value in values {
                remove_recomputable_hold_projections(value);
            }
        }
        Value::Object(values) => {
            for field in [
                "hold_rate",
                "hold_observation_count",
                "hold_evidence_reliability",
                "hold_quality_score",
                "hold_score_revision",
                "ticker_relative_quality_score",
                "ticker_relative_quality_status",
                "ticker_relative_quality_population_size",
                "ticker_relative_quality_reference_session",
                "ticker_relative_quality_revision",
                "ticker_relative_quality_distribution_hash",
            ] {
                values.remove(field);
            }
            for value in values.values_mut() {
                remove_recomputable_hold_projections(value);
            }
        }
        _ => {}
    }
}

/// Return the checkpoint exactly as its durable JSON representation decodes.
///
/// `serde_json::to_value` retains the full binary `f64` value while the JSON
/// text serializer emits the shortest decimal that round-trips to that value.
/// Certification must therefore use the latter: it is the representation that
/// ClickHouse stores and subsequent readers verify.
pub fn checkpoint_json_value(checkpoint: &GenericStructureCheckpoint) -> Result<Value, String> {
    let encoded = serde_json::to_string(checkpoint)
        .map_err(|error| format!("failed to serialize structure checkpoint: {error}"))?;
    serde_json::from_str(&encoded)
        .map_err(|error| format!("failed to decode serialized structure checkpoint: {error}"))
}

pub fn canonical_json_sha256(value: &Value) -> Result<String, String> {
    let mut bytes = Vec::new();
    write_canonical_json(&value, &mut bytes)?;
    Ok(sha256(&bytes))
}

pub fn split_sha256(checkpoint: &GenericStructureCheckpoint) -> Result<String, String> {
    let encoded = serde_json::to_string(&checkpoint.applied_split_adjustments)
        .map_err(|error| format!("failed to serialize structure splits: {error}"))?;
    let value = serde_json::from_str(&encoded)
        .map_err(|error| format!("failed to decode serialized structure splits: {error}"))?;
    let mut bytes = Vec::new();
    write_canonical_json(&value, &mut bytes)?;
    Ok(sha256(&bytes))
}

#[allow(clippy::too_many_arguments)]
pub fn build_checkpoint_certification(
    checkpoint: &GenericStructureCheckpoint,
    event_evidence: StructureReplayEvidence,
    session_date: NaiveDate,
    authority_start: DateTime<Utc>,
    source_plan_hash: &str,
    source_revision_token: &str,
    predecessor_checkpoint_sha256: String,
    predecessor_chain_sha256: String,
) -> Result<StructureCheckpointCertification, String> {
    let checkpoint_hash = checkpoint_sha256(checkpoint)?;
    let split_hash = split_sha256(checkpoint)?;
    let mut certification = StructureCheckpointCertification {
        schema_version: STRUCTURE_CERTIFICATION_REPLAY_SCHEMA_VERSION,
        event_evidence,
        split_sha256: split_hash,
        checkpoint_sha256: checkpoint_hash,
        predecessor_checkpoint_sha256,
        predecessor_chain_sha256,
        chain_sha256: String::new(),
        recovery_attestation: None,
    };
    certification.chain_sha256 = certification_chain_sha256(
        &certification,
        checkpoint,
        session_date,
        authority_start,
        source_plan_hash,
        source_revision_token,
    )?;
    Ok(certification)
}

#[allow(clippy::too_many_arguments)]
pub fn build_recovered_checkpoint_certification(
    checkpoint: &GenericStructureCheckpoint,
    event_evidence: StructureReplayEvidence,
    session_date: NaiveDate,
    authority_start: DateTime<Utc>,
    source_plan_hash: &str,
    source_revision_token: &str,
    predecessor_checkpoint_sha256: String,
    predecessor_chain_sha256: String,
    recovery_attestation: StructureCheckpointRecoveryAttestation,
) -> Result<StructureCheckpointCertification, String> {
    let policy_valid = match recovery_attestation.recovery_revision.as_str() {
        "execution-clock-zero-delayed-v1" => recovery_attestation
            .execution_clock_revision
            .contains("execution-clock-v1:"),
        "historical-sip-condition-recertification-v1" => recovery_attestation
            .source_policy_revision
            .contains("structure-input-v1:archive-sip-condition:"),
        _ => false,
    };
    if !policy_valid
        || recovery_attestation.delayed_trade_report_count != 0
        || recovery_attestation
            .source_checkpoint_set_id
            .trim()
            .is_empty()
        || !valid_sha256(&recovery_attestation.source_checkpoint_sha256)
        || !valid_sha256(&recovery_attestation.source_chain_sha256)
    {
        return Err("invalid structure checkpoint recovery attestation".to_string());
    }
    let checkpoint_hash = checkpoint_sha256(checkpoint)?;
    if recovery_attestation.source_checkpoint_sha256 != checkpoint_hash {
        return Err("recovery attestation does not bind the checkpoint payload".to_string());
    }
    let mut certification = StructureCheckpointCertification {
        schema_version: STRUCTURE_CERTIFICATION_SCHEMA_VERSION,
        event_evidence,
        split_sha256: split_sha256(checkpoint)?,
        checkpoint_sha256: checkpoint_hash,
        predecessor_checkpoint_sha256,
        predecessor_chain_sha256,
        chain_sha256: String::new(),
        recovery_attestation: Some(recovery_attestation),
    };
    certification.chain_sha256 = certification_chain_sha256(
        &certification,
        checkpoint,
        session_date,
        authority_start,
        source_plan_hash,
        source_revision_token,
    )?;
    Ok(certification)
}

pub fn validate_checkpoint_certification(
    certification: &StructureCheckpointCertification,
    checkpoint: &GenericStructureCheckpoint,
    session_date: NaiveDate,
    authority_start: DateTime<Utc>,
    source_plan_hash: &str,
    source_revision_token: &str,
) -> Result<(), String> {
    if !matches!(
        certification.schema_version,
        STRUCTURE_CERTIFICATION_REPLAY_SCHEMA_VERSION | STRUCTURE_CERTIFICATION_SCHEMA_VERSION
    ) {
        return Err("unsupported structure checkpoint certification schema".to_string());
    }
    match (
        certification.schema_version,
        &certification.recovery_attestation,
    ) {
        (STRUCTURE_CERTIFICATION_REPLAY_SCHEMA_VERSION, None) => {}
        (STRUCTURE_CERTIFICATION_SCHEMA_VERSION, Some(attestation))
            if matches!(
                attestation.recovery_revision.as_str(),
                "execution-clock-zero-delayed-v1" | "historical-sip-condition-recertification-v1"
            ) && attestation.delayed_trade_report_count == 0
                && !attestation.source_checkpoint_set_id.trim().is_empty()
                && valid_sha256(&attestation.source_checkpoint_sha256)
                && valid_sha256(&attestation.source_chain_sha256)
                && ((attestation.recovery_revision == "execution-clock-zero-delayed-v1"
                    && attestation
                        .execution_clock_revision
                        .contains("execution-clock-v1:"))
                    || (attestation.recovery_revision
                        == "historical-sip-condition-recertification-v1"
                        && attestation
                            .source_policy_revision
                            .contains("structure-input-v1:archive-sip-condition:")))
                && attestation.source_checkpoint_sha256 == certification.checkpoint_sha256 => {}
        _ => return Err("invalid structure checkpoint recovery certification".to_string()),
    }
    if certification.predecessor_checkpoint_sha256.is_empty()
        != certification.predecessor_chain_sha256.is_empty()
    {
        return Err("structure checkpoint certification chain is incomplete".to_string());
    }
    if !valid_sha256(&certification.event_evidence.event_sha256)
        || !valid_sha256(&certification.split_sha256)
        || !valid_sha256(&certification.checkpoint_sha256)
        || !valid_sha256(&certification.chain_sha256)
        || (!certification.predecessor_checkpoint_sha256.is_empty()
            && (!valid_sha256(&certification.predecessor_checkpoint_sha256)
                || !valid_sha256(&certification.predecessor_chain_sha256)))
    {
        return Err(
            "structure checkpoint certification contains an invalid SHA-256 value".to_string(),
        );
    }
    let actual_checkpoint_sha256 = checkpoint_sha256(checkpoint)?;
    if certification.checkpoint_sha256 != actual_checkpoint_sha256 {
        return Err(format!(
            "structure checkpoint payload hash mismatch: certified={}, actual={actual_checkpoint_sha256}",
            certification.checkpoint_sha256,
        ));
    }
    let actual_split_sha256 = split_sha256(checkpoint)?;
    if certification.split_sha256 != actual_split_sha256 {
        return Err(format!(
            "structure checkpoint split hash mismatch: certified={}, actual={actual_split_sha256}",
            certification.split_sha256,
        ));
    }
    let actual_chain_sha256 = certification_chain_sha256(
        certification,
        checkpoint,
        session_date,
        authority_start,
        source_plan_hash,
        source_revision_token,
    )?;
    if certification.chain_sha256 != actual_chain_sha256 {
        return Err(format!(
            "structure checkpoint chain hash mismatch: certified={}, actual={actual_chain_sha256}",
            certification.chain_sha256,
        ));
    }
    Ok(())
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 71
        && value.starts_with("sha256:")
        && value[7..].bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn certification_chain_sha256(
    certification: &StructureCheckpointCertification,
    checkpoint: &GenericStructureCheckpoint,
    session_date: NaiveDate,
    authority_start: DateTime<Utc>,
    source_plan_hash: &str,
    source_revision_token: &str,
) -> Result<String, String> {
    let value = serde_json::json!({
        "authority_start_us": authority_start.timestamp_micros(),
        "checkpoint_sha256": certification.checkpoint_sha256,
        "event_evidence": certification.event_evidence,
        "predecessor_chain_sha256": certification.predecessor_chain_sha256,
        "predecessor_checkpoint_sha256": certification.predecessor_checkpoint_sha256,
        "schema_version": certification.schema_version,
        "session_date": session_date,
        "source_plan_hash": source_plan_hash,
        "source_revision_token": source_revision_token,
        "split_sha256": certification.split_sha256,
        "recovery_attestation": certification.recovery_attestation,
        "sym": checkpoint.sym.to_ascii_uppercase(),
        "algorithm_version": checkpoint.algorithm_version,
    });
    let mut bytes = Vec::new();
    write_canonical_json(&value, &mut bytes)?;
    Ok(sha256(&bytes))
}

fn hash_compact_event(hasher: &mut Context, bytes: &mut Vec<u8>, event: &LiveCompactEvent) {
    bytes.clear();
    hash_upper_ascii_bytes(bytes, event.ticker.as_bytes());
    hash_bytes(bytes, event.event_date.as_bytes());
    bytes.extend_from_slice(&event.schema_version.to_le_bytes());
    bytes.extend_from_slice(&event.issue_flags.to_le_bytes());
    bytes.extend_from_slice(&event.arrival_sequence.to_le_bytes());
    bytes.extend_from_slice(&event.sip_timestamp_us.to_le_bytes());
    bytes.extend_from_slice(&event.execution_timestamp_us.to_le_bytes());
    bytes.extend_from_slice(&event.source_sequence.to_le_bytes());
    bytes.extend_from_slice(&[
        event.event_meta,
        event.exchange_primary,
        event.exchange_secondary,
    ]);
    bytes.extend_from_slice(&event.price_primary_int.to_le_bytes());
    bytes.extend_from_slice(&event.price_secondary_int.to_le_bytes());
    bytes.extend_from_slice(&event.size_primary.to_bits().to_le_bytes());
    bytes.extend_from_slice(&event.size_secondary.to_bits().to_le_bytes());
    bytes.extend_from_slice(&[
        event.condition_token_1,
        event.condition_token_2,
        event.condition_token_3,
        event.condition_token_4,
        event.condition_token_5,
    ]);
    hasher.update(bytes);
}

fn hash_bytes(bytes: &mut Vec<u8>, value: &[u8]) {
    bytes.extend_from_slice(&(value.len() as u64).to_le_bytes());
    bytes.extend_from_slice(value);
}

fn hash_upper_ascii_bytes(bytes: &mut Vec<u8>, value: &[u8]) {
    bytes.extend_from_slice(&(value.len() as u64).to_le_bytes());
    bytes.extend(value.iter().map(u8::to_ascii_uppercase));
}

fn sha256(value: &[u8]) -> String {
    let mut context = Context::new(&SHA256);
    context.update(value);
    format!("sha256:{}", hex(context.finish().as_ref()))
}

fn hex(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(value.len() * 2);
    for byte in value {
        result.push(HEX[(byte >> 4) as usize] as char);
        result.push(HEX[(byte & 0x0f) as usize] as char);
    }
    result
}

fn write_canonical_json(value: &Value, output: &mut Vec<u8>) -> Result<(), String> {
    match value {
        Value::Null => output.extend_from_slice(b"null"),
        Value::Bool(value) => output.extend_from_slice(if *value { b"true" } else { b"false" }),
        Value::Number(value) => output.extend_from_slice(value.to_string().as_bytes()),
        Value::String(value) => output.extend_from_slice(
            serde_json::to_string(value)
                .map_err(|error| format!("failed to encode canonical string: {error}"))?
                .as_bytes(),
        ),
        Value::Array(values) => {
            output.push(b'[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_canonical_json(value, output)?;
            }
            output.push(b']');
        }
        Value::Object(values) => {
            output.push(b'{');
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                output.extend_from_slice(
                    serde_json::to_string(key)
                        .map_err(|error| format!("failed to encode canonical key: {error}"))?
                        .as_bytes(),
                );
                output.push(b':');
                write_canonical_json(&values[key], output)?;
            }
            output.push(b'}');
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bars::TradeUpdateRule;
    use crate::compact_event::LIVE_COMPACT_EVENT_SCHEMA_VERSION;
    use crate::event::{MarketEvent, TradeEvent};
    use chrono::TimeZone;
    use serde_json::json;

    fn first_difference(left: &Value, right: &Value, path: &str) -> Option<String> {
        match (left, right) {
            (Value::Array(left), Value::Array(right)) => left
                .iter()
                .zip(right)
                .enumerate()
                .find_map(|(index, (left, right))| {
                    first_difference(left, right, &format!("{path}[{index}]"))
                }),
            (Value::Object(left), Value::Object(right)) => left
                .keys()
                .chain(right.keys())
                .find_map(|key| match (left.get(key), right.get(key)) {
                    (Some(left), Some(right)) => {
                        first_difference(left, right, &format!("{path}.{key}"))
                    }
                    _ => Some(format!("{path}.{key}")),
                }),
            _ if left == right => None,
            _ => Some(format!("{path}: {left} != {right}")),
        }
    }

    fn compact(ordinal: u64, sip: u64) -> LiveCompactEvent {
        LiveCompactEvent::from_persisted_fields(
            ordinal,
            1,
            2,
            3,
            4,
            5,
            "2026-08-21".to_string(),
            1,
            0,
            11,
            0,
            Utc.timestamp_micros(sip as i64).single().unwrap(),
            0,
            34500,
            0,
            LIVE_COMPACT_EVENT_SCHEMA_VERSION,
            sip,
            100.0,
            0.0,
            ordinal,
            "SUGP".to_string(),
        )
    }

    #[test]
    fn event_audit_is_chunk_independent_and_rejects_ordinal_gaps() {
        let events = [compact(10, 100), compact(11, 101), compact(12, 102)];
        let mut first = StructureEventAuditor::new(true);
        for event in &events {
            first.observe(event).unwrap();
        }
        let mut second = StructureEventAuditor::new(true);
        second.observe(&events[0]).unwrap();
        for event in &events[1..] {
            second.observe(event).unwrap();
        }
        assert_eq!(first.finish(), second.finish());

        let mut broken = StructureEventAuditor::new(true);
        broken.observe(&events[0]).unwrap();
        assert!(broken
            .observe(&events[2])
            .unwrap_err()
            .contains("ordinal discontinuity"));
    }

    #[test]
    fn canonical_json_hash_ignores_object_insertion_order() {
        let left = serde_json::json!({"b": 2, "a": 1});
        let mut right = serde_json::Map::new();
        right.insert("a".to_string(), serde_json::json!(1));
        right.insert("b".to_string(), serde_json::json!(2));
        let mut left_bytes = Vec::new();
        let mut right_bytes = Vec::new();
        write_canonical_json(&left, &mut left_bytes).unwrap();
        write_canonical_json(&Value::Object(right), &mut right_bytes).unwrap();
        assert_eq!(left_bytes, right_bytes);
    }

    #[test]
    fn checkpoint_certification_is_self_validating_and_chain_complete() {
        let checkpoint = crate::generic_structure::GenericStructureEngine::new("SUGP").checkpoint();
        let authority_start = Utc.with_ymd_and_hms(2026, 1, 2, 9, 0, 0).unwrap();
        let session_date = NaiveDate::from_ymd_opt(2026, 1, 2).unwrap();
        let evidence = StructureEventAuditor::new(true).finish();
        let certification = build_checkpoint_certification(
            &checkpoint,
            evidence,
            session_date,
            authority_start,
            "plan",
            "revision",
            String::new(),
            String::new(),
        )
        .unwrap();

        validate_checkpoint_certification(
            &certification,
            &checkpoint,
            session_date,
            authority_start,
            "plan",
            "revision",
        )
        .unwrap();

        let mut broken = certification.clone();
        broken.predecessor_checkpoint_sha256 = "sha256:missing-chain".to_string();
        assert!(validate_checkpoint_certification(
            &broken,
            &checkpoint,
            session_date,
            authority_start,
            "plan",
            "revision",
        )
        .unwrap_err()
        .contains("chain is incomplete"));
    }

    #[test]
    fn recovered_certification_requires_zero_delayed_clock_proof() {
        let checkpoint = crate::generic_structure::GenericStructureEngine::new("SUGP").checkpoint();
        let authority_start = Utc.with_ymd_and_hms(2026, 1, 2, 9, 0, 0).unwrap();
        let session_date = NaiveDate::from_ymd_opt(2026, 1, 2).unwrap();
        let checkpoint_hash = checkpoint_sha256(&checkpoint).unwrap();
        let attestation = StructureCheckpointRecoveryAttestation {
            recovery_revision: "execution-clock-zero-delayed-v1".to_string(),
            source_checkpoint_set_id: "legacy-v1".to_string(),
            source_checkpoint_sha256: checkpoint_hash,
            source_chain_sha256: format!("sha256:{}", "1".repeat(64)),
            source_policy_revision: String::new(),
            execution_clock_revision: "execution-clock-v1:1:10:0:1:now".to_string(),
            delayed_trade_report_count: 0,
        };
        let certification = build_recovered_checkpoint_certification(
            &checkpoint,
            StructureEventAuditor::new(true).finish(),
            session_date,
            authority_start,
            "plan",
            "revision:execution-clock-v1:1:10:0:1:now",
            String::new(),
            String::new(),
            attestation.clone(),
        )
        .unwrap();
        validate_checkpoint_certification(
            &certification,
            &checkpoint,
            session_date,
            authority_start,
            "plan",
            "revision:execution-clock-v1:1:10:0:1:now",
        )
        .unwrap();

        let mut delayed = attestation;
        delayed.delayed_trade_report_count = 1;
        assert!(build_recovered_checkpoint_certification(
            &checkpoint,
            StructureEventAuditor::new(true).finish(),
            session_date,
            authority_start,
            "plan",
            "revision:execution-clock-v1:1:10:1:1:now",
            String::new(),
            String::new(),
            delayed,
        )
        .unwrap_err()
        .contains("invalid structure checkpoint recovery attestation"));
    }

    #[test]
    fn recovered_certification_accepts_historical_sip_condition_policy() {
        let checkpoint = crate::generic_structure::GenericStructureEngine::new("SUGP").checkpoint();
        let authority_start = Utc.with_ymd_and_hms(2026, 1, 2, 9, 0, 0).unwrap();
        let session_date = NaiveDate::from_ymd_opt(2026, 1, 2).unwrap();
        let revision =
            "revision:structure-input-v1:archive-sip-condition:trade-condition-sha256:abc";
        let certification = build_recovered_checkpoint_certification(
            &checkpoint,
            StructureEventAuditor::new(true).finish(),
            session_date,
            authority_start,
            "plan",
            revision,
            String::new(),
            String::new(),
            StructureCheckpointRecoveryAttestation {
                recovery_revision: "historical-sip-condition-recertification-v1".to_string(),
                source_checkpoint_set_id: "canonical-v5".to_string(),
                source_checkpoint_sha256: checkpoint_sha256(&checkpoint).unwrap(),
                source_chain_sha256: format!("sha256:{}", "1".repeat(64)),
                source_policy_revision: revision.to_string(),
                execution_clock_revision: String::new(),
                delayed_trade_report_count: 0,
            },
        )
        .unwrap();

        validate_checkpoint_certification(
            &certification,
            &checkpoint,
            session_date,
            authority_start,
            "plan",
            revision,
        )
        .unwrap();
    }

    #[test]
    fn checkpoint_hash_matches_its_serialized_payload_after_market_updates() {
        let mut engine = crate::generic_structure::GenericStructureEngine::new("SUGP");
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        for (index, price) in [3.45, 3.46, 3.44, 3.48, 3.43, 3.51, 3.47]
            .into_iter()
            .cycle()
            .take(200)
            .enumerate()
        {
            let sequence = index as u64 + 1;
            engine.apply_event_without_snapshot(
                &MarketEvent::Trade(TradeEvent {
                    conditions: Vec::new(),
                    exchange: 1,
                    ingest_ts: start + chrono::Duration::milliseconds(index as i64),
                    participant_ts: None,
                    price,
                    raw: json!({"arrival_sequence": sequence}),
                    sequence,
                    size: 100.0 + index as f64,
                    tape: 3,
                    ticker: "SUGP".to_string(),
                    trade_id: sequence.to_string(),
                    trf_id: 0,
                    trf_ts: None,
                    ts: start + chrono::Duration::milliseconds(index as i64),
                }),
                TradeUpdateRule::regular(),
            );
        }
        let checkpoint = engine.checkpoint();
        let certified = checkpoint_sha256(&checkpoint).unwrap();
        let serialized = serde_json::to_string(&checkpoint).unwrap();
        let mut persisted = serde_json::from_str(&serialized).unwrap();
        remove_recomputable_hold_projections(&mut persisted);
        let in_memory = serde_json::to_value(&checkpoint).unwrap();
        let legacy_checkpoint: crate::generic_structure::GenericStructureCheckpoint =
            serde_json::from_value(persisted.clone()).unwrap();

        assert_eq!(
            certified,
            canonical_json_sha256(&persisted).unwrap(),
            "{}",
            first_difference(&in_memory, &persisted, "$").unwrap_or_default(),
        );
        assert_eq!(checkpoint_sha256(&legacy_checkpoint).unwrap(), certified);
    }

    #[test]
    fn derived_hold_projections_do_not_invalidate_legacy_checkpoint_certification() {
        let checkpoint = crate::generic_structure::GenericStructureEngine::new("SUGP").checkpoint();
        let hash = checkpoint_sha256(&checkpoint).unwrap();
        let mut legacy_value = serde_json::to_value(&checkpoint).unwrap();
        remove_recomputable_hold_projections(&mut legacy_value);
        let legacy_checkpoint: crate::generic_structure::GenericStructureCheckpoint =
            serde_json::from_value(legacy_value).unwrap();

        assert_eq!(checkpoint_sha256(&legacy_checkpoint).unwrap(), hash);
    }
}

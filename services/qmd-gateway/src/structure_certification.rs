use crate::compact_event::LiveCompactEvent;
use crate::generic_structure::GenericStructureCheckpoint;
use chrono::{DateTime, NaiveDate, Utc};
use ring::digest::{Context, SHA256};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const STRUCTURE_CERTIFICATION_SCHEMA_VERSION: u16 = 1;

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
    let value = serde_json::to_value(checkpoint)
        .map_err(|error| format!("failed to canonicalize structure checkpoint: {error}"))?;
    let mut bytes = Vec::new();
    write_canonical_json(&value, &mut bytes)?;
    Ok(sha256(&bytes))
}

pub fn split_sha256(checkpoint: &GenericStructureCheckpoint) -> Result<String, String> {
    let value = serde_json::to_value(&checkpoint.applied_split_adjustments)
        .map_err(|error| format!("failed to canonicalize structure splits: {error}"))?;
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
        schema_version: STRUCTURE_CERTIFICATION_SCHEMA_VERSION,
        event_evidence,
        split_sha256: split_hash,
        checkpoint_sha256: checkpoint_hash,
        predecessor_checkpoint_sha256,
        predecessor_chain_sha256,
        chain_sha256: String::new(),
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
    if certification.schema_version != STRUCTURE_CERTIFICATION_SCHEMA_VERSION {
        return Err("unsupported structure checkpoint certification schema".to_string());
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
    use crate::compact_event::LIVE_COMPACT_EVENT_SCHEMA_VERSION;
    use chrono::TimeZone;

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
}

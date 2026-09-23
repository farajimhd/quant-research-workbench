//! Bounded append-only decision records, identical for historical and live modes.
use crate::strategy_dispatch::Decision;
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
pub const MAX_BATCH: usize = 256;
pub const MAX_BYTES: usize = 8 * 1024 * 1024;
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Record {
    pub scope_hash: String,
    pub sequence: u64,
    pub payload_json: String,
}
impl Record {
    pub fn from_decision(d: &Decision) -> Result<Self> {
        if d.schema_version != 1
            || d.sequence == 0
            || d.actions.is_empty()
            || d.actions.len() > 8
            || d.decision_id
                != content_hash(&(&d.scope, &d.input, &d.safety, &d.actions, &d.evidence_hash))?
        {
            return Err(Error::Invalid(
                "invalid decision identity or journal version".into(),
            ));
        }
        let payload_json =
            serde_json::to_string(d).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_BYTES {
            return Err(Error::Capacity(
                "decision exceeds journal byte budget".into(),
            ));
        }
        Ok(Self {
            scope_hash: content_hash(&d.scope)?,
            sequence: d.sequence,
            payload_json,
        })
    }
    pub fn decode(&self) -> Result<Decision> {
        if self.payload_json.len() > MAX_BYTES {
            return Err(Error::Capacity("journal record byte budget".into()));
        }
        let d: Decision = serde_json::from_str(&self.payload_json)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let canonical = Self::from_decision(&d)?;
        if canonical.scope_hash != self.scope_hash
            || canonical.sequence != self.sequence
            || canonical.payload_json != self.payload_json
        {
            return Err(Error::Conflict(
                "journal key or canonical payload mismatch".into(),
            ));
        }
        Ok(d)
    }
}
#[derive(Debug, Clone)]
pub struct Batch {
    records: Vec<Record>,
}
impl Batch {
    pub fn new(decisions: &[Decision]) -> Result<Self> {
        if decisions.is_empty() || decisions.len() > MAX_BATCH {
            return Err(Error::Capacity("journal batch count outside 1..256".into()));
        }
        let mut records = Vec::with_capacity(decisions.len());
        let mut bytes = 0;
        for d in decisions {
            let record = Record::from_decision(d)?;
            bytes += record.payload_json.len();
            if bytes > MAX_BYTES {
                return Err(Error::Capacity("journal batch exceeds byte budget".into()));
            }
            records.push(record);
        }
        for pair in records.windows(2) {
            if pair[0].scope_hash != pair[1].scope_hash
                || pair[0].sequence.checked_add(1) != Some(pair[1].sequence)
            {
                return Err(Error::Invalid(
                    "journal batch must be contiguous in one scope".into(),
                ));
            }
        }
        Ok(Self { records })
    }
    pub fn records(&self) -> &[Record] {
        &self.records
    }
    /// Duplicate physical retry rows are permitted only when byte-identical.
    pub fn verify_readback(&self, rows: &[Record]) -> Result<()> {
        let mut seen = vec![false; self.records.len()];
        let first = self.records[0].sequence;
        for row in rows {
            row.decode()?;
            let index = row
                .sequence
                .checked_sub(first)
                .and_then(|v| usize::try_from(v).ok())
                .filter(|i| *i < seen.len())
                .ok_or_else(|| Error::Conflict("unexpected journal readback sequence".into()))?;
            let expected = &self.records[index];
            if row.scope_hash != expected.scope_hash || row.payload_json != expected.payload_json {
                return Err(Error::Conflict("conflicting journal decision".into()));
            }
            seen[index] = true;
        }
        if seen.iter().any(|v| !*v) {
            return Err(Error::Unready("journal readback incomplete".into()));
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_dispatch::*;
    use crate::strategy_lifecycle::Phase;
    fn decisions() -> Vec<Decision> {
        let mut s = State::new(Scope {
            run_id: "r".into(),
            mode: Mode::Backtest,
            account: "a".into(),
            strategy_instance: "s".into(),
            strategy_kind: crate::strategy_dispatch::StrategyKind::GenericCandidate,
            instrument: 1,
            code_hash: "code".into(),
            config_hash: "config".into(),
        })
        .unwrap();
        let safety = Safety {
            position_quantity: 0,
            pending_exit_quantity: 0,
            exit_pending: false,
            pending_entry: false,
            last_exit_reason: None,
            flatten: false,
            protective_stop_crossed: false,
            manual_exit: false,
            completed_macd_reversal: false,
            setup_phase: Phase::Building,
            luld_buffer_reached: false,
            encounter_exit: false,
            early_setup_failed: false,
            structural_exit: false,
        };
        (1..=2)
            .map(|i| {
                s.evaluate(
                    InputBoundary {
                        event_id: format!("e{i}"),
                        event_time_ns: i,
                        available_at_ns: i,
                        evaluated_at_ns: i,
                        source_sequence: i,
                        feature_hash: "f".into(),
                    },
                    &safety,
                    "proof".into(),
                    || {
                        Ok(vec![Action::Wait {
                            reason: "gate".into(),
                        }])
                    },
                )
                .unwrap()
            })
            .collect()
    }
    #[test]
    fn retry_readback_requires_all_records_and_preserves_bytes() {
        let b = Batch::new(&decisions()).unwrap();
        let mut rows = b.records().to_vec();
        rows.push(rows[0].clone());
        b.verify_readback(&rows).unwrap();
        assert!(b.verify_readback(&rows[..1]).is_err());
        assert_eq!(rows[0].decode().unwrap().sequence, 1);
    }
    #[test]
    fn tampering_and_sequence_gaps_fail() {
        let mut ds = decisions();
        ds[1].sequence = 3;
        assert!(Batch::new(&ds).is_err());
        let b = Batch::new(&decisions()).unwrap();
        let mut r = b.records()[0].clone();
        r.sequence = 2;
        assert!(r.decode().is_err());
        let mut ds = decisions();
        ds[0].safety.manual_exit = true;
        assert!(Batch::new(&ds).is_err());
    }
}

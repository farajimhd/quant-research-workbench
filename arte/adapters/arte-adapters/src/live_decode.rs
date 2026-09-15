//! Bounded frame normalization and per-instrument/channel latency assessment.
use crate::{massive, massive_stream::ReceivedFrame};
use arte_core::events::{EventKind, Observation, Receipt};
use arte_core::latency::{Assessment, LatencyMonitor, LatencyPolicy};
use arte_core::{Error, Result};
use serde_json::Value;
use std::collections::BTreeMap;
#[derive(Debug)]
pub struct AuditedEvent {
    pub observation: Observation,
    pub sip_latency: Assessment,
    pub participant_latency: Option<Assessment>,
    pub exposure_permitted: bool,
}
#[derive(Clone)]
struct Monitors {
    sip: LatencyMonitor,
    participant: LatencyMonitor,
}
pub struct Decoder {
    run_id: String,
    lane: u16,
    sequence: u64,
    last_monotonic_ns: u64,
    maximum_events: usize,
    maximum_lanes: usize,
    policy: LatencyPolicy,
    require_participant: bool,
    monitors: BTreeMap<(u64, EventKind), Monitors>,
    failed: bool,
}
impl Decoder {
    pub fn new(
        run_id: String,
        lane: u16,
        maximum_events: usize,
        maximum_lanes: usize,
        policy: LatencyPolicy,
        require_participant: bool,
    ) -> Result<Self> {
        policy.validate()?;
        if run_id.is_empty() || maximum_events == 0 || maximum_events > 100000 || maximum_lanes == 0
        {
            return Err(Error::Invalid("invalid live decode bounds or run".into()));
        }
        Ok(Self {
            run_id,
            lane,
            sequence: 0,
            last_monotonic_ns: 0,
            maximum_events,
            maximum_lanes,
            policy,
            require_participant,
            monitors: BTreeMap::new(),
            failed: false,
        })
    }
    pub fn failed(&self) -> bool {
        self.failed
    }
    /// Resolver must use source-time instrument identity known by receive time.
    /// Errors return no partial frame. Caller retains the raw frame for audit/repair.
    /// A failed decoder remains blocked until a new validated run is established.
    pub fn decode(
        &mut self,
        frame: &ReceivedFrame,
        now_monotonic_ns: u64,
        clock_uncertainty_ns: u64,
        mut resolve: impl FnMut(&str, u64, u64) -> Result<u64>,
    ) -> Result<Vec<AuditedEvent>> {
        if self.failed {
            return Err(Error::Unready(
                "live decoder failed; recovery run required".into(),
            ));
        }
        let result = self.decode_inner(frame, now_monotonic_ns, clock_uncertainty_ns, &mut resolve);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn decode_inner(
        &mut self,
        frame: &ReceivedFrame,
        now: u64,
        uncertainty: u64,
        resolve: &mut impl FnMut(&str, u64, u64) -> Result<u64>,
    ) -> Result<Vec<AuditedEvent>> {
        if frame.text.len() > 32 * 1024 * 1024
            || now < frame.monotonic_ns
            || frame.monotonic_ns < self.last_monotonic_ns
        {
            return Err(Error::Invalid(
                "invalid live frame size or monotonic clock".into(),
            ));
        }
        let rows: Vec<Value> = serde_json::from_str(&frame.text)
            .map_err(|_| Error::Invalid("invalid raw market frame".into()))?;
        if rows.is_empty() || rows.len() > self.maximum_events {
            return Err(Error::Capacity("live frame event budget exceeded".into()));
        }
        let mut observations = Vec::with_capacity(rows.len());
        let mut sequence = self.sequence;
        for row in rows {
            let kind = match row.get("ev").and_then(Value::as_str) {
                Some("T") => EventKind::Trade,
                Some("Q") => EventKind::Quote,
                Some("status") => continue,
                _ => return Err(Error::Invalid("unexpected normalization channel".into())),
            };
            let symbol = row
                .get("sym")
                .and_then(Value::as_str)
                .ok_or_else(|| Error::Invalid("stream event lacks symbol".into()))?;
            let sip = row
                .get("t")
                .and_then(Value::as_u64)
                .and_then(|v| v.checked_mul(1_000_000))
                .ok_or_else(|| Error::Invalid("invalid stream SIP clock".into()))?;
            let instrument = resolve(symbol, sip, frame.utc_ns)?;
            sequence = sequence
                .checked_add(1)
                .ok_or_else(|| Error::Capacity("live application sequence exhausted".into()))?;
            observations.push(massive::normalize(
                &row,
                instrument,
                kind,
                true,
                frame.utc_ns,
                Some(Receipt {
                    run_id: self.run_id.clone(),
                    lane: self.lane,
                    sequence,
                    utc_ns: frame.utc_ns,
                    monotonic_ns: frame.monotonic_ns,
                }),
            )?);
        }
        // Clone only lanes touched by this frame. Shared market state is not copied.
        let mut touched: BTreeMap<(u64, EventKind), Monitors> = BTreeMap::new();
        let mut new_lanes = 0;
        let mut output = Vec::with_capacity(observations.len());
        for observation in observations {
            let key = (observation.key.instrument, observation.key.kind);
            if let std::collections::btree_map::Entry::Vacant(slot) = touched.entry(key) {
                let monitors = if let Some(current) = self.monitors.get(&key) {
                    current.clone()
                } else {
                    new_lanes += 1;
                    if self.monitors.len() + new_lanes > self.maximum_lanes {
                        return Err(Error::Capacity("live latency lane budget exceeded".into()));
                    }
                    Monitors {
                        sip: LatencyMonitor::new(self.policy.clone())?,
                        participant: LatencyMonitor::new(self.policy.clone())?,
                    }
                };
                slot.insert(monitors);
            }
            let monitors = touched.get_mut(&key).unwrap();
            let queue = now - frame.monotonic_ns;
            let mut sip_latency =
                monitors
                    .sip
                    .observe(observation.sip.ns, frame.utc_ns, uncertainty, queue, now);
            sip_latency.lower_age_ns = sip_latency
                .lower_age_ns
                .saturating_sub(u64::from(observation.sip.precision_ns) - 1);
            let participant_latency = observation.participant.map(|t| {
                let mut assessment =
                    monitors
                        .participant
                        .observe(t.ns, frame.utc_ns, uncertainty, queue, now);
                assessment.lower_age_ns = assessment
                    .lower_age_ns
                    .saturating_sub(u64::from(t.precision_ns) - 1);
                assessment
            });
            let exposure_permitted = monitors.sip.permits_exposure()
                && match participant_latency {
                    Some(_) => monitors.participant.permits_exposure(),
                    None => !self.require_participant,
                };
            output.push(AuditedEvent {
                observation,
                sip_latency,
                participant_latency,
                exposure_permitted,
            });
        }
        self.monitors.extend(touched);
        self.sequence = sequence;
        self.last_monotonic_ns = frame.monotonic_ns;
        Ok(output)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn decoder(required: bool) -> Decoder {
        Decoder::new(
            "run".into(),
            1,
            16,
            8,
            LatencyPolicy {
                warn_ns: 2_000_000,
                block_ns: 5_000_000,
                max_clock_uncertainty_ns: 1_000_000,
                recovery_samples: 1,
                repeat_ns: 1_000_000,
            },
            required,
        )
        .unwrap()
    }
    fn frame() -> ReceivedFrame {
        ReceivedFrame{text:r#"[{"ev":"T","sym":"AAPL","t":1000,"q":1,"p":10,"s":1,"x":1,"i":"trade"},{"ev":"Q","sym":"AAPL","t":1000,"q":2,"bp":9.99,"ap":10.01,"bs":10,"as":10,"bx":1,"ax":1}]"#.into(),utc_ns:1_000_000_100,monotonic_ns:100,frame_sequence:1}
    }
    #[test]
    fn frame_events_get_distinct_application_sequences_and_original_receipt() {
        let events = decoder(false)
            .decode(&frame(), 100, 0, |s, _, _| {
                assert_eq!(s, "AAPL");
                Ok(1)
            })
            .unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].observation.receipt.as_ref().unwrap().sequence, 1);
        assert_eq!(events[1].observation.receipt.as_ref().unwrap().sequence, 2);
        assert!(events[0].exposure_permitted);
        assert!(events[0].observation.participant.is_none());
    }
    #[test]
    fn missing_participant_is_not_fabricated_and_can_block() {
        let events = decoder(true)
            .decode(&frame(), 100, 0, |_, _, _| Ok(1))
            .unwrap();
        assert!(!events[0].exposure_permitted);
        assert!(events[0].participant_latency.is_none());
    }
    #[test]
    fn local_queue_delay_blocks_even_when_provider_timestamp_is_fresh() {
        let events = decoder(false)
            .decode(&frame(), 10_000_100, 0, |_, _, _| Ok(1))
            .unwrap();
        assert!(!events[0].exposure_permitted);
        assert!(events[0].sip_latency.notify);
    }
    #[test]
    fn unresolved_symbol_rejects_whole_frame_and_latches_failure() {
        let mut d = decoder(false);
        let mut count = 0;
        assert!(d
            .decode(&frame(), 100, 0, |_, _, _| {
                count += 1;
                if count == 2 {
                    Err(Error::Unready("identity unavailable".into()))
                } else {
                    Ok(1)
                }
            })
            .is_err());
        assert_eq!(d.sequence, 0);
        assert!(d.failed());
        assert!(d.monitors.is_empty());
    }
}

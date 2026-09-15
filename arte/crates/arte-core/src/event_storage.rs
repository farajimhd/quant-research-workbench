//! Lossless bounded staging contract. Physical codecs and provider-key acceptance
//! remain deployment gates; constructing a batch grants no writer permission.
use crate::events::{EventStore, Observation, ObservationRef, Payload};
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const MAX_OBSERVATIONS: usize = 8192;
pub const MAX_BYTES: usize = 16 * 1024 * 1024;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PayloadObject {
    pub hash: String,
    pub payload: Payload,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Manifest {
    pub schema_version: u16,
    pub payload_hashes: Vec<String>,
    /// Preserves application order within the acquisition batch. Not a dense ID.
    pub observation_hashes: Vec<String>,
}
#[derive(Debug, Clone)]
pub struct Batch {
    manifest: Manifest,
    payloads: Vec<PayloadObject>,
    observations: Vec<ObservationRef>,
}
impl Batch {
    /// Rebuild a persisted batch only from a pinned manifest and complete readback.
    /// Physical row order is irrelevant; the manifest retains acquisition order.
    pub fn restore(
        expected_id: &str,
        manifest: Manifest,
        payloads: &[PayloadObject],
        observations: &[ObservationRef],
    ) -> Result<Self> {
        if manifest.schema_version != 1
            || manifest.payload_hashes.is_empty()
            || manifest.payload_hashes.len() > MAX_OBSERVATIONS
            || manifest.observation_hashes.is_empty()
            || manifest.observation_hashes.len() > MAX_OBSERVATIONS
            || manifest
                .payload_hashes
                .iter()
                .chain(&manifest.observation_hashes)
                .any(|h| {
                    h.len() != 64
                        || !h
                            .bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                })
            || manifest
                .payload_hashes
                .windows(2)
                .any(|pair| pair[0] >= pair[1])
            || manifest
                .observation_hashes
                .iter()
                .collect::<BTreeSet<_>>()
                .len()
                != manifest.observation_hashes.len()
        {
            return Err(Error::Invalid("invalid event storage manifest".into()));
        }
        if content_hash(&manifest)? != expected_id {
            return Err(Error::Conflict(
                "event storage manifest identity mismatch".into(),
            ));
        }
        let mut batch = Self {
            manifest,
            payloads: vec![],
            observations: vec![],
        };
        batch.verify_readback(payloads, observations)?;
        let payload_index: BTreeMap<_, _> = payloads.iter().map(|p| (&p.hash, p)).collect();
        let observation_index: BTreeMap<_, _> = observations
            .iter()
            .map(|o| Ok((content_hash(o)?, o)))
            .collect::<Result<_>>()?;
        batch.payloads = batch
            .manifest
            .payload_hashes
            .iter()
            .map(|h| payload_index[h].clone())
            .collect();
        batch.observations = batch
            .manifest
            .observation_hashes
            .iter()
            .map(|h| observation_index[h].clone())
            .collect();
        Ok(batch)
    }
    pub fn prepare(input: &[Observation]) -> Result<Self> {
        if input.is_empty() || input.len() > MAX_OBSERVATIONS {
            return Err(Error::Capacity(
                "event batch count outside supported bounds".into(),
            ));
        }
        let mut store = EventStore::new(MAX_OBSERVATIONS)?;
        let mut bytes = 0usize;
        for event in input {
            // Bound before retaining another clone. This also bounds unusual strings/arrays.
            bytes = bytes
                .checked_add(
                    serde_json::to_vec(event)
                        .map_err(|e| Error::Serialization(e.to_string()))?
                        .len(),
                )
                .ok_or_else(|| Error::Capacity("event batch byte overflow".into()))?;
            if bytes > MAX_BYTES {
                return Err(Error::Capacity("event batch byte budget exceeded".into()));
            }
            store.insert(event.clone())?;
        }
        let payloads: Vec<_> = store
            .payloads
            .into_iter()
            .map(|(hash, payload)| PayloadObject { hash, payload })
            .collect();
        let observations = store.observations;
        let manifest = Manifest {
            schema_version: 1,
            payload_hashes: payloads.iter().map(|o| o.hash.clone()).collect(),
            observation_hashes: observations
                .iter()
                .map(content_hash)
                .collect::<Result<_>>()?,
        };
        let batch = Self {
            manifest,
            payloads,
            observations,
        };
        batch.verify_readback(&batch.payloads, &batch.observations)?;
        Ok(batch)
    }
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }
    pub fn id(&self) -> Result<String> {
        content_hash(&self.manifest)
    }
    pub fn payloads(&self) -> &[PayloadObject] {
        &self.payloads
    }
    pub fn observations(&self) -> &[ObservationRef] {
        &self.observations
    }
    /// Immutable retry rows may repeat; missing, conflicting or foreign rows fail.
    /// Callers must query only this manifest's identities, not an arbitrary range.
    pub fn verify_readback(
        &self,
        payloads: &[PayloadObject],
        observations: &[ObservationRef],
    ) -> Result<()> {
        if payloads.len() > MAX_OBSERVATIONS * 2 || observations.len() > MAX_OBSERVATIONS * 2 {
            return Err(Error::Capacity("event readback row budget exceeded".into()));
        }
        let expected_payloads: BTreeSet<_> = self.manifest.payload_hashes.iter().collect();
        let mut found_payloads = BTreeSet::new();
        let mut decoded = BTreeMap::new();
        let mut bytes = 0usize;
        for object in payloads {
            bounded_bytes(object, &mut bytes)?;
            object.payload.validate()?;
            if !expected_payloads.contains(&object.hash)
                || content_hash(&object.payload)? != object.hash
            {
                return Err(Error::Conflict("event payload identity mismatch".into()));
            }
            found_payloads.insert(&object.hash);
            decoded.insert(&object.hash, &object.payload);
        }
        if expected_payloads != found_payloads {
            return Err(Error::Unready("event payload readback incomplete".into()));
        }
        let expected: BTreeSet<_> = self.manifest.observation_hashes.iter().collect();
        let mut found = BTreeSet::new();
        let mut receipt_slots = BTreeMap::new();
        for reference in observations {
            bounded_bytes(reference, &mut bytes)?;
            let hash = content_hash(reference)?;
            if !expected.contains(&hash) {
                return Err(Error::Conflict("unexpected event observation".into()));
            }
            let payload = decoded
                .get(&reference.payload_hash)
                .ok_or_else(|| Error::Unready("observation payload missing".into()))?;
            hydrate(reference, payload).validate()?;
            if let Some(receipt) = &reference.receipt {
                let slot = (&receipt.run_id, receipt.lane, receipt.sequence);
                if receipt_slots
                    .insert(slot, hash.clone())
                    .is_some_and(|old| old != hash)
                {
                    return Err(Error::Conflict(
                        "live receipt slot reused for another observation".into(),
                    ));
                }
            }
            found.insert(hash);
        }
        if expected.into_iter().cloned().collect::<BTreeSet<_>>() != found {
            return Err(Error::Unready(
                "event observation readback incomplete".into(),
            ));
        }
        Ok(())
    }
    pub fn hydrate(&self) -> Result<Vec<Observation>> {
        self.verify_readback(&self.payloads, &self.observations)?;
        let payloads: BTreeMap<_, _> = self
            .payloads
            .iter()
            .map(|o| (&o.hash, &o.payload))
            .collect();
        Ok(self
            .observations
            .iter()
            .map(|r| hydrate(r, payloads[&r.payload_hash]))
            .collect())
    }
}
fn bounded_bytes(value: &impl Serialize, total: &mut usize) -> Result<()> {
    *total = total
        .checked_add(
            serde_json::to_vec(value)
                .map_err(|e| Error::Serialization(e.to_string()))?
                .len(),
        )
        .ok_or_else(|| Error::Capacity("event readback byte overflow".into()))?;
    // Two physical retry copies plus hash/reference overhead; still strictly bounded.
    if *total > MAX_BYTES * 3 {
        return Err(Error::Capacity(
            "event readback byte budget exceeded".into(),
        ));
    }
    Ok(())
}
fn hydrate(reference: &ObservationRef, payload: &Payload) -> Observation {
    Observation {
        key: reference.key.clone(),
        payload: payload.clone(),
        sip: reference.sip,
        participant: reference.participant,
        available_at_ns: reference.available_at_ns,
        receipt: reference.receipt.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{Decimal, EventKey, EventKind, Receipt, SourceTime};
    fn event() -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 42,
                session: 20260915,
                kind: EventKind::Trade,
                sequence: 100,
            },
            payload: Payload::Trade {
                price: Decimal::parse("10.01").unwrap(),
                size: Decimal::parse("1.125").unwrap(),
                exchange: 1,
                trade_id: "trade".into(),
                trf: None,
                conditions: vec![1, 2],
                correction: None,
            },
            sip: SourceTime {
                ns: 1_000_000,
                precision_ns: 1_000_000,
            },
            participant: None,
            available_at_ns: 2_000_000,
            receipt: Some(Receipt {
                run_id: "run".into(),
                lane: 1,
                sequence: 1,
                utc_ns: 2_000_000,
                monotonic_ns: 100,
            }),
        }
    }
    #[test]
    fn live_and_rest_share_payload_without_rewriting_live_clocks() {
        let live = event();
        let mut rest = live.clone();
        rest.receipt = None;
        rest.available_at_ns = 3_000_000;
        rest.participant = Some(SourceTime {
            ns: 999_999,
            precision_ns: 1,
        });
        let b = Batch::prepare(&[live.clone(), rest.clone(), live.clone()]).unwrap();
        assert_eq!(b.payloads.len(), 1);
        assert_eq!(b.observations.len(), 2);
        assert_eq!(b.hydrate().unwrap(), vec![live, rest]);
        let mut duplicate_rows = b.observations.clone();
        duplicate_rows.extend(b.observations.clone());
        b.verify_readback(&b.payloads, &duplicate_rows).unwrap();
    }
    #[test]
    fn incomplete_or_corrupt_readback_cannot_acknowledge() {
        let b = Batch::prepare(&[event()]).unwrap();
        assert!(b.verify_readback(&[], &b.observations).is_err());
        assert!(b.verify_readback(&b.payloads, &[]).is_err());
        let mut bad = b.payloads.clone();
        if let Payload::Trade { size, .. } = &mut bad[0].payload {
            size.atoms += 1;
        }
        assert!(b.verify_readback(&bad, &b.observations).is_err());
    }
    #[test]
    fn receipt_slot_collision_rejected_but_source_correction_preserved() {
        let a = event();
        let mut b = a.clone();
        if let Payload::Trade { price, .. } = &mut b.payload {
            price.atoms += 1;
        }
        assert!(Batch::prepare(&[a.clone(), b.clone()]).is_err());
        b.receipt.as_mut().unwrap().sequence = 2;
        let batch = Batch::prepare(&[a, b]).unwrap();
        assert_eq!(batch.payloads.len(), 2);
        assert_eq!(batch.observations.len(), 2);
    }
    #[test]
    fn restore_checks_manifest_and_preserves_order_before_merges() {
        let a = event();
        let mut b = a.clone();
        b.key.sequence = 99;
        b.receipt.as_mut().unwrap().sequence = 2;
        let original = Batch::prepare(&[a.clone(), b.clone()]).unwrap();
        let mut rows = original.observations.clone();
        rows.reverse();
        rows.extend(original.observations.clone());
        let recovered = Batch::restore(
            &original.id().unwrap(),
            original.manifest.clone(),
            &original.payloads,
            &rows,
        )
        .unwrap();
        assert_eq!(recovered.hydrate().unwrap(), vec![a, b]);
        let mut changed = original.manifest.clone();
        changed.observation_hashes.reverse();
        assert!(
            Batch::restore(&original.id().unwrap(), changed, &original.payloads, &rows).is_err()
        );
    }
}

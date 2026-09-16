//! Exact quote recovery, not evidence that the feed is connected or fresh.
use super::*;
use crate::seed_storage::Object;
use serde::{Deserialize, Serialize};
use std::{io::Write, sync::Arc};
const MAXIMUM_BYTES: usize = 1024 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    context_hash: String,
    scope: (u16, u64, u32),
    policy_hash: String,
    latest: Option<Observation>,
}
#[derive(Serialize)]
struct View<'a> {
    version: u32,
    context_hash: &'a str,
    scope: (u16, u64, u32),
    policy_hash: &'a str,
    latest: &'a Option<Observation>,
}
fn context(hash: &str, maximum_bytes: usize) -> Result<()> {
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || maximum_bytes == 0
        || maximum_bytes > MAXIMUM_BYTES
    {
        return Err(Error::Invalid(
            "quote recovery context or byte budget".into(),
        ));
    }
    Ok(())
}
struct Writer {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Write for Writer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("quote recovery byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl Book {
    /// The context must identify the parent run/cut. A faulted book cannot be
    /// certified; recover from an earlier verified cut and reconcile the fault.
    pub fn checkpoint(&self, context_hash: &str, maximum_bytes: usize) -> Result<Object> {
        context(context_hash, maximum_bytes)?;
        if self.failed {
            return Err(Error::Unready(
                "faulted quote book cannot checkpoint".into(),
            ));
        }
        let snapshot = View {
            version: 1,
            context_hash,
            scope: (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            policy_hash: self.policy_hash()?,
            latest: &self.latest,
        };
        let mut writer = Writer {
            bytes: Vec::new(),
            maximum: maximum_bytes,
        };
        serde_json::to_writer(&mut writer, &snapshot)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Object::new(writer.bytes))
    }
    /// Expected identities and the policy come from the committed parent graph.
    /// This never rewrites receipt, availability, participant or SIP timestamps.
    pub fn restore_checkpoint(
        object: &Object,
        expected_hash: &str,
        context_hash: &str,
        scope: Scope,
        policy: Arc<eligibility::Pinned>,
        maximum_bytes: usize,
    ) -> Result<Self> {
        context(context_hash, maximum_bytes)?;
        if object.payload.len() > maximum_bytes || object.id != expected_hash {
            return Err(Error::Invalid("quote recovery identity or size".into()));
        }
        object.verify()?;
        let snapshot: Snapshot = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if snapshot.version != 1
            || snapshot.context_hash != context_hash
            || snapshot.scope != (scope.provider, scope.instrument, scope.session)
            || snapshot.policy_hash != policy.hash()
        {
            return Err(Error::Conflict(
                "quote recovery scope or policy differs".into(),
            ));
        }
        let mut book = Self::new(scope)?;
        book.bind_shared_policy(policy)?;
        if let Some(latest) = snapshot.latest {
            // Observation validation is intentional. Executability is checked at
            // decision time, so an empty/crossed/ineligible quote stays visible.
            book.observe(&latest)?;
        }
        if book.checkpoint(context_hash, maximum_bytes)?.payload != object.payload {
            return Err(Error::Invalid("noncanonical quote recovery image".into()));
        }
        Ok(book)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{EventKey, Receipt, SourceTime};
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        }
    }
    fn policy() -> Arc<eligibility::Pinned> {
        let p = eligibility::Policy {
            provider: 1,
            valid_from_ns: 1,
            valid_to_ns: 1000,
            available_at_ns: 1,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: Default::default(),
            allowed_indicators: Default::default(),
            allow_empty_conditions: true,
            allow_empty_indicators: true,
        };
        let hash = crate::content_hash(&p).unwrap();
        Arc::new(eligibility::Pinned::new(p, &hash).unwrap())
    }
    fn quote(sequence: u64, bid: i64, ask: i64) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Quote,
                sequence,
            },
            payload: Payload::Quote {
                bid: Decimal {
                    atoms: bid,
                    scale: 2,
                },
                ask: Decimal {
                    atoms: ask,
                    scale: 2,
                },
                bid_size: Decimal {
                    atoms: 10,
                    scale: 0,
                },
                ask_size: Decimal {
                    atoms: 10,
                    scale: 0,
                },
                bid_exchange: 1,
                ask_exchange: 1,
                conditions: vec![],
                indicators: vec![],
            },
            sip: SourceTime {
                ns: 100 + sequence,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 102 + sequence,
            receipt: Some(Receipt {
                run_id: "live".into(),
                lane: 1,
                sequence,
                utc_ns: 102 + sequence,
                monotonic_ns: sequence,
            }),
        }
    }
    #[test]
    fn restore_preserves_receipts_age_duplicates_and_unusable_quotes() {
        let context = "b".repeat(64);
        let mut book = Book::new(scope()).unwrap();
        book.bind_shared_policy(policy()).unwrap();
        let first = quote(1, 100, 101);
        book.observe(&first).unwrap();
        let image = book.checkpoint(&context, 10000).unwrap();
        let mut restored =
            Book::restore_checkpoint(&image, &image.id, &context, scope(), policy(), 10000)
                .unwrap();
        assert_eq!(restored.latest(), Some(&first));
        assert!(restored.require_executable(103, 10).is_ok());
        assert!(restored.require_executable(111, 10).is_err());
        let mut duplicate = first.clone();
        duplicate.available_at_ns = 110;
        duplicate.receipt.as_mut().unwrap().utc_ns = 110;
        assert_eq!(restored.observe(&duplicate).unwrap(), Update::Duplicate);
        assert_eq!(restored.latest(), Some(&first));
        assert!(restored.require_executable(111, 10).is_err());
        let crossed = quote(2, 102, 101);
        restored.observe(&crossed).unwrap();
        let image = restored.checkpoint(&context, 10000).unwrap();
        let restored =
            Book::restore_checkpoint(&image, &image.id, &context, scope(), policy(), 10000)
                .unwrap();
        assert_eq!(restored.latest(), Some(&crossed));
        assert!(restored.require_executable(104, 10).is_err());
    }
    #[test]
    fn mismatched_identity_scope_policy_and_faulted_capture_are_rejected() {
        let context = "b".repeat(64);
        let mut book = Book::new(scope()).unwrap();
        assert!(book.checkpoint(&context, 10000).is_err());
        book.bind_shared_policy(policy()).unwrap();
        let empty = book.checkpoint(&context, 10000).unwrap();
        assert!(
            Book::restore_checkpoint(&empty, &empty.id, &context, scope(), policy(), 10000)
                .unwrap()
                .latest()
                .is_none()
        );
        book.observe(&quote(1, 100, 101)).unwrap();
        let image = book.checkpoint(&context, 10000).unwrap();
        assert!(book.checkpoint(&context, 10).is_err());
        assert!(Book::restore_checkpoint(
            &image,
            &image.id,
            &"c".repeat(64),
            scope(),
            policy(),
            10000
        )
        .is_err());
        assert!(Book::restore_checkpoint(
            &image,
            &image.id,
            &context,
            Scope {
                instrument: 2,
                ..scope()
            },
            policy(),
            10000
        )
        .is_err());
        let mut altered: Snapshot = serde_json::from_slice(&image.payload).unwrap();
        altered.policy_hash = "c".repeat(64);
        let altered = Object::new(serde_json::to_vec(&altered).unwrap());
        assert!(Book::restore_checkpoint(
            &altered,
            &altered.id,
            &context,
            scope(),
            policy(),
            10000
        )
        .is_err());
        let mut bytes = image.payload.clone();
        bytes.push(b' ');
        let altered = Object::new(bytes);
        assert!(Book::restore_checkpoint(
            &altered,
            &altered.id,
            &context,
            scope(),
            policy(),
            10000
        )
        .is_err());
        assert!(book.observe(&quote(1, 99, 101)).is_err());
        assert!(book.checkpoint(&context, 10000).is_err());
    }
}

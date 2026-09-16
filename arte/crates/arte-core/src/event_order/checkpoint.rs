//! Partial-release recovery. Restoring queued evidence behind the watermark does
//! not permit a newly arriving event behind that watermark.
use super::*;
use crate::seed_storage::Object;
use serde::{Deserialize, Serialize};
use std::io::Write;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    context_hash: String,
    scope: (u16, u64, u32),
    maximum: usize,
    watermark_ns: u64,
    pending: Vec<Observation>,
}
#[derive(Serialize)]
struct View<'a> {
    version: u32,
    context_hash: &'a str,
    scope: (u16, u64, u32),
    maximum: usize,
    watermark_ns: u64,
    pending: Vec<&'a Observation>,
}
fn validate_context(hash: &str, maximum_bytes: usize) -> Result<()> {
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
        || maximum_bytes == 0
        || maximum_bytes > 64 * 1024 * 1024
    {
        return Err(Error::Invalid(
            "ordering recovery context or byte budget".into(),
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
            return Err(std::io::Error::other("ordering recovery byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl Buffer {
    pub fn checkpoint(&self, context_hash: &str, maximum_bytes: usize) -> Result<Object> {
        validate_context(context_hash, maximum_bytes)?;
        if self.failed {
            return Err(Error::Unready(
                "faulted ordering buffer cannot checkpoint".into(),
            ));
        }
        let image = View {
            version: 1,
            context_hash,
            scope: (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            maximum: self.maximum,
            watermark_ns: self.watermark_ns,
            pending: self.pending_events().collect(),
        };
        let mut writer = Writer {
            bytes: Vec::new(),
            maximum: maximum_bytes,
        };
        serde_json::to_writer(&mut writer, &image)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Object::new(writer.bytes))
    }
    /// The caller pins this image with the same computation/journal cut. The
    /// buffer does not know whether a consumer already applied its pending head.
    pub fn restore_checkpoint(
        object: &Object,
        expected_hash: &str,
        context_hash: &str,
        scope: Scope,
        maximum: usize,
        maximum_bytes: usize,
    ) -> Result<Self> {
        validate_context(context_hash, maximum_bytes)?;
        if object.id != expected_hash || object.payload.len() > maximum_bytes {
            return Err(Error::Invalid("ordering recovery identity or size".into()));
        }
        object.verify()?;
        let image: Snapshot = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if image.version != 1
            || image.context_hash != context_hash
            || image.scope != (scope.provider, scope.instrument, scope.session)
            || image.maximum != maximum
            || image.pending.len() > maximum
        {
            return Err(Error::Conflict(
                "ordering recovery scope or capacity differs".into(),
            ));
        }
        // Reconstruct the already-admitted queue before installing the certified
        // watermark. Do not feed these records through live admission at that cut.
        let mut buffer = Self::new(scope, maximum, 0)?;
        for event in image.pending {
            if !buffer.push(&event)? {
                return Err(Error::Conflict(
                    "duplicate event in ordering recovery".into(),
                ));
            }
        }
        buffer.begin_release(image.watermark_ns)?;
        if buffer.checkpoint(context_hash, maximum_bytes)?.payload != object.payload {
            return Err(Error::Invalid(
                "noncanonical ordering recovery image".into(),
            ));
        }
        Ok(buffer)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{Decimal, EventKind, Payload, SourceTime};
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        }
    }
    fn event(at: u64) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Trade,
                sequence: at,
            },
            payload: Payload::Trade {
                price: Decimal {
                    atoms: 100,
                    scale: 2,
                },
                size: Decimal { atoms: 1, scale: 0 },
                exchange: 1,
                trade_id: at.to_string(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at + 1,
            receipt: None,
        }
    }
    #[test]
    fn interrupted_release_restores_remaining_prefix_without_relaxing_admission() {
        let context = "a".repeat(64);
        let mut original = Buffer::new(scope(), 8, 0).unwrap();
        for at in [30, 10, 20, 40] {
            original.push(&event(at)).unwrap();
        }
        let mut applied = Vec::new();
        assert!(original
            .release(40, |e| {
                if e.sip.ns == 20 {
                    return Err(Error::Unready("consumer paused".into()));
                }
                applied.push(e.key.sequence);
                Ok(())
            })
            .is_err());
        assert_eq!(applied, vec![10]);
        let image = original.checkpoint(&context, 10000).unwrap();
        let restore =
            || Buffer::restore_checkpoint(&image, &image.id, &context, scope(), 8, 10000).unwrap();
        let mut restored = restore();
        assert_eq!(restored.first_releasable().unwrap(), &event(20));
        let mut duplicate = event(20);
        duplicate.available_at_ns = 90;
        assert!(!restored.push(&duplicate).unwrap());
        assert_eq!(restored.first_releasable().unwrap().available_at_ns, 21);
        for buffer in [&mut original, &mut restored] {
            let mut emitted = Vec::new();
            assert_eq!(
                buffer
                    .release(40, |e| {
                        emitted.push(e.key.sequence);
                        Ok(())
                    })
                    .unwrap(),
                2
            );
            assert_eq!(emitted, vec![20, 30]);
            assert_eq!(buffer.pending(), 1);
            assert!(buffer.first_releasable().is_none());
        }
        assert_eq!(
            original.checkpoint(&context, 10000).unwrap().id,
            restored.checkpoint(&context, 10000).unwrap().id
        );
        let mut late = restore();
        assert!(late.push(&event(25)).is_err());
        assert!(late.failed());
        assert!(late.checkpoint(&context, 10000).is_err());
    }
    #[test]
    fn duplicate_reordered_mismatched_and_oversized_images_fail() {
        let context = "a".repeat(64);
        let mut buffer = Buffer::new(scope(), 8, 0).unwrap();
        buffer.push(&event(10)).unwrap();
        buffer.push(&event(20)).unwrap();
        let image = buffer.checkpoint(&context, 10000).unwrap();
        assert!(buffer.checkpoint(&context, 8).is_err());
        assert!(
            Buffer::restore_checkpoint(&image, &image.id, &context, scope(), 7, 10000).is_err()
        );
        assert!(
            Buffer::restore_checkpoint(&image, &image.id, &"b".repeat(64), scope(), 8, 10000)
                .is_err()
        );
        for duplicate in [false, true] {
            let mut altered: Snapshot = serde_json::from_slice(&image.payload).unwrap();
            if duplicate {
                altered.pending.push(altered.pending[0].clone());
            } else {
                altered.pending.reverse();
            }
            let altered = Object::new(serde_json::to_vec(&altered).unwrap());
            assert!(
                Buffer::restore_checkpoint(&altered, &altered.id, &context, scope(), 8, 10000)
                    .is_err()
            );
        }
        let mut corrupt = image.clone();
        corrupt.payload.push(b' ');
        assert!(
            Buffer::restore_checkpoint(&corrupt, &image.id, &context, scope(), 8, 10000).is_err()
        );
    }
}

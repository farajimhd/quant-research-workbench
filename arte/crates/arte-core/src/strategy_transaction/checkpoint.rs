//! Transaction recovery retains prepared state without treating it as committed.
use super::*;
use crate::seed_storage::Object;
use serde::{de::DeserializeOwned, Deserialize};
use std::io::Write;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Prepared<S> {
    state: S,
    decision: Decision,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot<S> {
    version: u32,
    context: String,
    scope: Scope,
    maximum_state_bytes: usize,
    state: S,
    last: Option<Decision>,
    pending: Option<Prepared<S>>,
}
fn bounds(context: &str, maximum: usize) -> Result<()> {
    if context.len() != 64
        || !context
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || maximum == 0
        || maximum > 64 * 1024 * 1024
    {
        return Err(Error::Invalid("strategy recovery context or budget".into()));
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
            return Err(std::io::Error::other("strategy checkpoint byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl<S: Clone + Serialize> Runtime<S> {
    pub fn checkpoint(&self, context: &str, maximum_bytes: usize) -> Result<Object> {
        bounds(context, maximum_bytes)?;
        let snapshot = Snapshot {
            version: 1,
            context: context.into(),
            scope: self.scope().clone(),
            maximum_state_bytes: self.maximum_state_bytes,
            state: &self.state,
            last: self.last_committed.as_ref().map(|(_, d)| d.clone()),
            pending: self.pending.as_ref().map(|p| Prepared {
                state: &p.next_state,
                decision: p.decision.clone(),
            }),
        };
        let mut writer = Writer {
            bytes: Vec::new(),
            maximum: maximum_bytes,
        };
        serde_json::to_writer(&mut writer, &snapshot)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Object::new(writer.bytes))
    }
}
impl<S: Clone + Serialize + DeserializeOwned> Runtime<S> {
    /// readback is the independently read last committed journal batch. A prepared
    /// decision stays pending even if its rows were written before a crash.
    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        image: &Object,
        expected_hash: &str,
        context: &str,
        scope: &Scope,
        maximum_state_bytes: usize,
        maximum_bytes: usize,
        readback: &[Record],
    ) -> Result<(Self, Option<Committed>)> {
        bounds(context, maximum_bytes)?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Invalid("strategy recovery identity or bytes".into()));
        }
        image.verify()?;
        let snapshot: Snapshot<S> = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if snapshot.version != 1
            || snapshot.context != context
            || &snapshot.scope != scope
            || snapshot.maximum_state_bytes != maximum_state_bytes
        {
            return Err(Error::Conflict("strategy recovery pins differ".into()));
        }
        let mut runtime = Self::new(snapshot.scope, snapshot.state, maximum_state_bytes)?;
        let receipt = if let Some(last) = snapshot.last {
            Batch::new(std::slice::from_ref(&last))?.verify_readback(readback)?;
            runtime.dispatch = Dispatch::from_last_decision(scope.clone(), &last)?;
            let request = content_hash(&(&last.input, &last.safety, &last.evidence_hash))?;
            runtime.last_committed = Some((request, last.clone()));
            Some(Committed { decision: last })
        } else {
            if !readback.is_empty() {
                return Err(Error::Conflict("unexpected genesis journal rows".into()));
            }
            None
        };
        if let Some(pending) = snapshot.pending {
            check_state(&pending.state, maximum_state_bytes)?;
            let d = pending.decision;
            let request_hash = content_hash(&(&d.input, &d.safety, &d.evidence_hash))?;
            if runtime
                .last_committed
                .as_ref()
                .is_some_and(|(hash, _)| hash == &request_hash)
                && content_hash(&pending.state)? != content_hash(&runtime.state)?
            {
                return Err(Error::Conflict("replayed strategy state differs".into()));
            }
            let mut next_dispatch = runtime.dispatch.clone();
            let calculated = next_dispatch.evaluate(
                d.input.clone(),
                &d.safety,
                d.evidence_hash.clone(),
                || Ok(d.actions.clone()),
            )?;
            if content_hash(&calculated)? != content_hash(&d)? {
                return Err(Error::Conflict("prepared recovery decision differs".into()));
            }
            runtime.pending = Some(Pending {
                request_hash,
                next_state: pending.state,
                next_dispatch,
                batch: Batch::new(std::slice::from_ref(&d))?,
                decision: d,
            });
        }
        if runtime.checkpoint(context, maximum_bytes)?.payload != image.payload {
            return Err(Error::Conflict("noncanonical strategy checkpoint".into()));
        }
        Ok((runtime, receipt))
    }
}

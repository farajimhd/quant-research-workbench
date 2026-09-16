//! A checkpoint is not evidence of a journal commit. Reverified transaction
//! receipts must independently reproduce the checkpoint's exact consumer ledger.
use super::*;
use crate::seed_storage::Object;
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    context_hash: String,
    input_hash: String,
    accounts: BTreeMap<String, Option<String>>,
}
fn bounds(context: &str, maximum: usize) -> Result<()> {
    if context.len() != 64
        || !context
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || maximum == 0
        || maximum > 1024 * 1024
    {
        return Err(Error::Invalid(
            "account barrier recovery pins or budget".into(),
        ));
    }
    Ok(())
}
impl Barrier {
    pub fn checkpoint(&self, context: &str, maximum_bytes: usize) -> Result<Object> {
        bounds(context, maximum_bytes)?;
        if self.finished {
            return Err(Error::Unready(
                "acknowledged barrier cannot checkpoint".into(),
            ));
        }
        // Population is bounded to 4096; each key and decision digest is 64 bytes.
        let snapshot = Snapshot {
            version: 1,
            context_hash: context.into(),
            input_hash: content_hash(&self.input)?,
            accounts: self.accounts.clone(),
        };
        let bytes =
            serde_json::to_vec(&snapshot).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > maximum_bytes {
            return Err(Error::Capacity(
                "account barrier recovery byte budget".into(),
            ));
        }
        Ok(Object::new(bytes))
    }
    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        image: &Object,
        expected_hash: &str,
        context: &str,
        input: InputBoundary,
        scopes: &[Scope],
        maximum_accounts: usize,
        maximum_bytes: usize,
        receipts: &[&Committed],
    ) -> Result<Self> {
        bounds(context, maximum_bytes)?;
        if image.id != expected_hash
            || image.payload.len() > maximum_bytes
            || receipts.len() > scopes.len()
        {
            return Err(Error::Invalid(
                "account barrier recovery identity or budget".into(),
            ));
        }
        image.verify()?;
        let snapshot: Snapshot = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut restored = Self::new(input, scopes, maximum_accounts)?;
        if snapshot.version != 1
            || snapshot.context_hash != context
            || snapshot.input_hash != content_hash(&restored.input)?
            || snapshot.accounts.keys().ne(restored.accounts.keys())
        {
            return Err(Error::Conflict(
                "account barrier recovery scope differs".into(),
            ));
        }
        for receipt in receipts {
            if !restored.record(receipt)? {
                return Err(Error::Conflict("duplicate recovery receipt".into()));
            }
        }
        if restored.accounts != snapshot.accounts
            || restored.checkpoint(context, maximum_bytes)?.payload != image.payload
        {
            return Err(Error::Conflict(
                "account barrier recovery receipts differ".into(),
            ));
        }
        Ok(restored)
    }
}

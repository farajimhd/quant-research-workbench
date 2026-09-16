//! Portable run-policy input. This is not a strategy approval or live permission.
use super::Config;
use crate::{content_hash, run_manifest::Pinned, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const MAXIMUM_BYTES: usize = 16 * 1024 * 1024;
pub const MAXIMUM_CONSUMERS: usize = 4096;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Consumer {
    pub account: String,
    pub instrument: u64,
    pub strategy_instance: String,
    pub config: Config,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Document {
    pub schema_version: u32,
    pub manifest_hash: String,
    pub consumers: Vec<Consumer>,
}
impl Document {
    /// Decode a bounded complete document. Serde rejects duplicate struct fields.
    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > MAXIMUM_BYTES {
            return Err(Error::Capacity(
                "candidate policy document byte budget".into(),
            ));
        }
        serde_json::from_slice(bytes)
            .map_err(|error| Error::Invalid(format!("candidate policy document: {error}")))
    }
    /// Resolve human-readable identities against the independently pinned manifest.
    /// The configured owner subsequently verifies effective policy hashes against
    /// actual market features and quote policy; this method does not replace it.
    pub fn bind(self, manifest: &Pinned) -> Result<BTreeMap<String, Config>> {
        if self.schema_version != 1 || self.manifest_hash != manifest.hash() {
            return Err(Error::Conflict("candidate policy document identity".into()));
        }
        if self.consumers.is_empty() || self.consumers.len() > MAXIMUM_CONSUMERS {
            return Err(Error::Capacity("candidate policy consumer budget".into()));
        }
        if self.consumers.len() != manifest.manifest().consumers.len() {
            return Err(Error::Conflict("candidate policy consumer count".into()));
        }
        let mut configs = BTreeMap::new();
        for consumer in self.consumers {
            consumer.config.policy()?;
            let scope = manifest.scope(
                &consumer.account,
                consumer.instrument,
                &consumer.strategy_instance,
            )?;
            if configs
                .insert(content_hash(&scope)?, consumer.config)
                .is_some()
            {
                return Err(Error::Conflict(
                    "duplicate candidate policy consumer".into(),
                ));
            }
        }
        Ok(configs)
    }
}

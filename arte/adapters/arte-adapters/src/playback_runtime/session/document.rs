//! Portable fresh-session inputs, not a checkpoint or authorization receipt.
use super::*;
use serde::{Deserialize, Serialize};
use std::io::Read;

pub const MAXIMUM_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Document {
    pub schema_version: u32,
    pub manifest_hash: String,
    #[serde(deserialize_with = "unique_map")]
    pub configurations: BTreeMap<String, Config>,
    #[serde(deserialize_with = "unique_map")]
    pub accounts: BTreeMap<String, Account>,
    pub price_scale: u8,
    pub fill_model: simulation_model::Model,
    pub cost_model: simulation_costs::Model,
    pub limits: Limits,
}
fn unique_map<'de, D, T>(deserializer: D) -> std::result::Result<BTreeMap<String, T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct Entries<T>(std::marker::PhantomData<T>);
    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for Entries<T> {
        type Value = BTreeMap<String, T>;
        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("at most 4096 unique startup entries")
        }
        fn visit_map<A: serde::de::MapAccess<'de>>(
            self,
            mut access: A,
        ) -> std::result::Result<Self::Value, A::Error> {
            let mut entries = BTreeMap::new();
            while let Some(key) = access.next_key::<String>()? {
                if entries.len() == 4096 || entries.contains_key(&key) {
                    return Err(serde::de::Error::custom(
                        "duplicate or excessive startup entries",
                    ));
                }
                entries.insert(key, access.next_value()?);
            }
            Ok(entries)
        }
    }
    deserializer.deserialize_map(Entries(std::marker::PhantomData))
}
impl Document {
    /// Snapshot configuration only. No runtime state or credentials are captured.
    pub fn from_request(request: &Request<'_>) -> Self {
        Self {
            schema_version: 1,
            manifest_hash: request.manifest.hash().into(),
            configurations: request.configurations.clone(),
            accounts: request.accounts.clone(),
            price_scale: request.price_scale,
            fill_model: request.fill_model.clone(),
            cost_model: request.cost_model.clone(),
            limits: request.limits.clone(),
        }
    }
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.manifest_hash.len() != 64
            || !self
                .manifest_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || self.accounts.is_empty()
            || self.accounts.len() > 4096
            || self.configurations.is_empty()
            || self.configurations.len() > 4096
        {
            return Err(Error::Invalid("backtest startup document shape".into()));
        }
        let bytes = serde_json::to_vec(self).map_err(|e| Error::Invalid(e.to_string()))?;
        if bytes.len() > MAXIMUM_BYTES {
            return Err(Error::Capacity("backtest startup document bytes".into()));
        }
        arte_core::content_hash(&("arte.backtest-startup.v1", self))
    }
    /// The caller must supply the expected hash from independent run metadata.
    /// Decoding alone does not validate effective strategy or market bindings.
    pub fn decode(bytes: &[u8], expected_hash: &str) -> Result<Self> {
        if bytes.len() > MAXIMUM_BYTES {
            return Err(Error::Capacity("backtest startup document bytes".into()));
        }
        let document: Self =
            serde_json::from_slice(bytes).map_err(|e| Error::Invalid(e.to_string()))?;
        if document.hash()? != expected_hash {
            return Err(Error::Conflict(
                "backtest startup document hash differs".into(),
            ));
        }
        Ok(document)
    }
    pub fn read(reader: impl Read, expected_hash: &str) -> Result<Self> {
        let mut bytes = Vec::new();
        reader
            .take(MAXIMUM_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| Error::Unready(format!("backtest startup read: {e}")))?;
        Self::decode(&bytes, expected_hash)
    }
    pub(super) fn into_request(self, manifest: &Pinned) -> Request<'_> {
        Request {
            manifest,
            configurations: self.configurations,
            accounts: self.accounts,
            price_scale: self.price_scale,
            fill_model: self.fill_model,
            cost_model: self.cost_model,
            limits: self.limits,
        }
    }
}

//! Exact published common-cut receipt. Not evidence of power-loss durability;
//! issuing adapters must enforce the separately approved storage acceptance gate.
use super::*;

#[derive(Debug)]
pub struct Published {
    root: String,
    manifest: String,
    cut: Cut,
}
pub struct Owners<'a> {
    pub controller: &'a mut Runtime,
    pub candidates: &'a mut Candidates,
    pub portfolio: &'a mut Portfolio,
    pub manifest: &'a Pinned,
    pub last_fills: &'a BTreeMap<String, Fill>,
    pub currencies: &'a BTreeMap<u64, SettlementCurrency>,
    pub limits: &'a Limits,
}
/// Immutable graph captured only after every decision and action has completed.
/// Partial recovery captures must not occupy the immutable boundary slot.
pub struct Finalized {
    bundle: Bundle,
}
impl Finalized {
    pub fn capture(owners: Owners<'_>, cut: &Cut) -> Result<Self> {
        owners.controller.decision_view()?;
        owners.controller.actions.require_complete()?;
        let receipts = owners.candidates.registered_receipts(owners.controller)?;
        if receipts.len() != owners.manifest.manifest().consumers.len() {
            return Err(Error::Unready("boundary decisions incomplete".into()));
        }
        Ok(Self {
            bundle: Bundle::capture(
                owners.controller,
                owners.candidates,
                owners.portfolio,
                owners.manifest,
                cut,
                owners.last_fills,
                owners.currencies,
                owners.limits,
            )?,
        })
    }
    pub fn bundle(&self) -> &Bundle {
        &self.bundle
    }
}
impl Published {
    /// Crate-internal: call only after complete storage readback and semantic
    /// restore validation. Serialized input cannot construct a publication receipt.
    pub(crate) fn verified(root: &str, manifest: &Pinned, cut: &Cut) -> Result<Self> {
        if root.len() != 64
            || !root
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("published checkpoint identity".into()));
        }
        Ok(Self {
            root: root.into(),
            manifest: manifest.hash().into(),
            cut: cut.clone(),
        })
    }
    pub fn root(&self) -> &str {
        &self.root
    }
    pub fn cut(&self) -> &Cut {
        &self.cut
    }
    /// Synchronous exclusive recapture prevents state changes between comparison
    /// and acknowledgment. Pending actions and unverified rejection receipts still
    /// block the controller even when the captured graph matches this receipt.
    pub fn acknowledge(&self, owners: Owners<'_>) -> Result<()> {
        if owners.manifest.hash() != self.manifest {
            return Err(Error::Conflict(
                "published checkpoint manifest differs".into(),
            ));
        }
        let current = Bundle::capture(
            owners.controller,
            owners.candidates,
            owners.portfolio,
            owners.manifest,
            &self.cut,
            owners.last_fills,
            owners.currencies,
            owners.limits,
        )?;
        if current.root.id != self.root {
            return Err(Error::Conflict(
                "runtime state differs from published checkpoint".into(),
            ));
        }
        owners.controller.acknowledge()
    }
}

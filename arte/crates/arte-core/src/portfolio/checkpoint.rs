//! A portfolio-only recovery image. The coordinator must publish a matching cut
//! for execution, strategy and source state before treating it as run recovery.
use super::*;
use crate::{run_manifest::Pinned, seed_storage::Object, strategy_dispatch::Mode};
use std::{collections::BTreeSet, io::Write};
pub mod storage;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cut {
    pub boundary_sequence: u64,
    pub boundary_hash: String,
    pub at_ns: u64,
}
pub struct Limits {
    pub maximum_accounts: usize,
    pub maximum_reservations: usize,
    pub maximum_settlements: usize,
    pub maximum_bytes: usize,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    schema_version: u32,
    manifest_hash: String,
    cut: Cut,
    accounts: BTreeMap<String, AccountState>,
}
#[derive(Serialize)]
struct SnapshotView<'a> {
    schema_version: u32,
    manifest_hash: &'a str,
    cut: &'a Cut,
    accounts: BTreeMap<&'a str, &'a AccountState>,
}
fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}
fn check_context(run: &Pinned, cut: &Cut, limits: &Limits) -> Result<()> {
    if run.manifest().mode != Mode::Backtest
        || cut.boundary_sequence == 0
        || !hash_valid(&cut.boundary_hash)
        || limits.maximum_accounts == 0
        || limits.maximum_accounts > 100_000
        || limits.maximum_reservations == 0
        || limits.maximum_reservations > 1_000_000
        || limits.maximum_settlements == 0
        || limits.maximum_settlements > 1_000_000
        || limits.maximum_bytes == 0
        || limits.maximum_bytes > 64 * 1024 * 1024
    {
        return Err(Error::Invalid(
            "portfolio checkpoint context or limits invalid".into(),
        ));
    }
    Ok(())
}
fn validate(snapshot: &Snapshot, run: &Pinned, cut: &Cut, limits: &Limits) -> Result<()> {
    check_context(run, cut, limits)?;
    if snapshot.schema_version != 1 || snapshot.manifest_hash != run.hash() || &snapshot.cut != cut
    {
        return Err(Error::Conflict(
            "portfolio checkpoint manifest or cut differs".into(),
        ));
    }
    let expected: BTreeSet<_> = run
        .manifest()
        .consumers
        .iter()
        .map(|c| c.account.as_str())
        .collect();
    if snapshot.accounts.len() > limits.maximum_accounts
        || snapshot
            .accounts
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>()
            != expected
    {
        return Err(Error::Conflict(
            "portfolio checkpoint account population differs".into(),
        ));
    }
    let instruments: BTreeSet<_> = run
        .manifest()
        .consumers
        .iter()
        .map(|c| (c.account.as_str(), c.instrument))
        .collect();
    let mut reservations = 0_usize;
    let mut settlements = 0_usize;
    for (id, state) in &snapshot.accounts {
        let account = &state.account;
        reservations = reservations
            .checked_add(account.reservations.len())
            .ok_or_else(|| Error::Capacity("reservation count overflow".into()))?;
        settlements = settlements
            .checked_add(state.settlements.len())
            .ok_or_else(|| Error::Capacity("settlement count overflow".into()))?;
        if reservations > limits.maximum_reservations || settlements > limits.maximum_settlements {
            return Err(Error::Capacity("portfolio checkpoint row budget".into()));
        }
        if account.simulation_run_id.as_deref() != Some(run.manifest().run_id.as_str())
            || account.budget_minor == 0
            || account.max_balance_age_ns == 0
            || account.balance_at_ns > cut.at_ns
            || account.currency.len() != 3
            || !account.currency.bytes().all(|v| v.is_ascii_uppercase())
            || account.currency_scale > 9
        {
            return Err(Error::Invalid("checkpoint account mandate invalid".into()));
        }
        for (command, reservation) in &account.reservations {
            if command.is_empty()
                || command.len() > 128
                || command != &reservation.command_id
                || reservation.cash_minor == 0
                || !instruments.contains(&(id.as_str(), reservation.instrument))
                || state.settlements.contains_key(command)
            {
                return Err(Error::Conflict(
                    "checkpoint reservation invalid or already settled".into(),
                ));
            }
        }
        if state
            .settlements
            .iter()
            .any(|(id, hash)| id.is_empty() || id.len() > 128 || !hash_valid(hash))
        {
            return Err(Error::Invalid(
                "checkpoint settlement receipt invalid".into(),
            ));
        }
    }
    Ok(())
}
struct BoundedBytes {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Write for BoundedBytes {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("checkpoint byte capacity exceeded"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn encode(snapshot: &impl Serialize, maximum: usize) -> Result<Vec<u8>> {
    let mut output = BoundedBytes {
        bytes: Vec::new(),
        maximum,
    };
    serde_json::to_writer(&mut output, snapshot)
        .map_err(|e| Error::Serialization(e.to_string()))?;
    Ok(output.bytes)
}
impl Portfolio {
    /// Require complete funding coverage, not merely membership of known orders.
    /// An exclusive borrow prevents reservations changing during this check.
    pub fn require_checkpoint_funding(
        &mut self,
        expected: &BTreeMap<String, (BTreeSet<String>, BTreeSet<String>)>,
    ) -> Result<()> {
        if !self.accounts.keys().eq(expected.keys()) {
            return Err(Error::Conflict("checkpoint funding accounts differ".into()));
        }
        for (account, state) in &mut self.accounts {
            let state = state
                .get_mut()
                .map_err(|_| Error::Unready("account lock poisoned".into()))?;
            let (reserved, settled) = &expected[account];
            if !state.account.reservations.keys().eq(reserved.iter())
                || !state.settlements.keys().eq(settled.iter())
            {
                return Err(Error::Conflict(
                    "unowned portfolio checkpoint funding".into(),
                ));
            }
        }
        Ok(())
    }
    pub fn checkpoint(&self, run: &Pinned, cut: &Cut, limits: &Limits) -> Result<Object> {
        check_context(run, cut, limits)?;
        if self.accounts.len() > limits.maximum_accounts {
            return Err(Error::Capacity("portfolio account budget".into()));
        }
        // Stable account order prevents lock-order inversion. Ordinary operations
        // hold only one account lock. This captures one consistent portfolio cut.
        let guards = self
            .accounts
            .iter()
            .map(|(id, state)| {
                state
                    .lock()
                    .map(|guard| (id, guard))
                    .map_err(|_| Error::Unready("account lock poisoned".into()))
            })
            .collect::<Result<Vec<_>>>()?;
        let mut reservations = 0_usize;
        let mut settlements = 0_usize;
        for (_, state) in &guards {
            reservations = reservations.saturating_add(state.reservations.len());
            settlements = settlements.saturating_add(state.settlements.len());
        }
        if reservations > limits.maximum_reservations || settlements > limits.maximum_settlements {
            return Err(Error::Capacity("portfolio checkpoint row budget".into()));
        }
        let snapshot = SnapshotView {
            schema_version: 1,
            manifest_hash: run.hash(),
            cut,
            accounts: guards
                .iter()
                .map(|(id, state)| (id.as_str(), &**state))
                .collect(),
        };
        let payload = encode(&snapshot, limits.maximum_bytes)?;
        drop(snapshot);
        drop(guards);
        let snapshot: Snapshot =
            serde_json::from_slice(&payload).map_err(|e| Error::Serialization(e.to_string()))?;
        validate(&snapshot, run, cut, limits)?;
        Ok(Object::new(payload))
    }
    pub fn restore_checkpoint(
        run: &Pinned,
        cut: &Cut,
        object: &Object,
        expected_id: &str,
        limits: &Limits,
    ) -> Result<Self> {
        check_context(run, cut, limits)?;
        if !hash_valid(expected_id)
            || object.id != expected_id
            || object.payload.len() > limits.maximum_bytes
        {
            return Err(Error::Conflict(
                "portfolio checkpoint identity or byte budget differs".into(),
            ));
        }
        object.verify()?;
        let snapshot: Snapshot = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        validate(&snapshot, run, cut, limits)?;
        if encode(&snapshot, limits.maximum_bytes)? != object.payload {
            return Err(Error::Invalid("noncanonical portfolio checkpoint".into()));
        }
        Ok(Self {
            accounts: snapshot
                .accounts
                .into_iter()
                .map(|(id, state)| (id, Mutex::new(state)))
                .collect(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::run_manifest::{Clock, Consumer, Execution, Manifest};
    pub(super) fn fixture() -> (Portfolio, Pinned, Cut, Limits, SimulatedSettlement) {
        let manifest = Manifest {
            schema_version: 1,
            run_id: "r".into(),
            mode: Mode::Backtest,
            code_release_hash: "a".repeat(64),
            source_manifest_hash: "a".repeat(64),
            reference_manifest_hash: "a".repeat(64),
            seed_manifest_hash: "a".repeat(64),
            algorithm_manifest_hash: "a".repeat(64),
            dependency_plan_hash: "a".repeat(64),
            hardware_profile_hash: "a".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "a".repeat(64),
                cost_model_hash: "a".repeat(64),
            },
            consumers: vec![Consumer {
                account: "a".into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: "a".repeat(64),
            }],
        };
        let hash = manifest.hash().unwrap();
        let run = Pinned::new(manifest, &hash).unwrap();
        let reservation = Reservation {
            command_id: "c".into(),
            instrument: 1,
            cash_minor: 50,
        };
        let portfolio = Portfolio::new(BTreeMap::from([(
            "a".into(),
            Account {
                currency: "USD".into(),
                currency_scale: 2,
                simulation_run_id: Some("r".into()),
                budget_minor: 100,
                broker_available_minor: 100,
                balance_at_ns: 1,
                max_balance_age_ns: 100,
                reservations: BTreeMap::from([("c".into(), reservation.clone())]),
            },
        )]))
        .unwrap();
        let request = SimulatedSettlement {
            run_id: "r".into(),
            reservation,
            currency: "USD".into(),
            currency_scale: 2,
            net_cash_minor: -10,
            at_ns: 2,
            evidence_hash: "a".repeat(64),
        };
        (
            portfolio,
            run,
            Cut {
                boundary_sequence: 3,
                boundary_hash: "b".repeat(64),
                at_ns: 3,
            },
            Limits {
                maximum_accounts: 1,
                maximum_reservations: 2,
                maximum_settlements: 2,
                maximum_bytes: 10000,
            },
            request,
        )
    }
    #[test]
    fn complete_funding_requires_exact_reserved_and_settled_populations() {
        let (mut portfolio, _, _, _, request) = fixture();
        let mut expected = BTreeMap::from([(
            "a".into(),
            (
                BTreeSet::from([request.reservation.command_id.clone()]),
                BTreeSet::new(),
            ),
        )]);
        portfolio.require_checkpoint_funding(&expected).unwrap();
        assert!(portfolio
            .require_checkpoint_funding(&BTreeMap::new())
            .is_err());
        portfolio.settle_simulated("a", &request, 2).unwrap();
        assert!(portfolio.require_checkpoint_funding(&expected).is_err());
        let state = expected.get_mut("a").unwrap();
        state.0.clear();
        state.1.insert(request.reservation.command_id);
        portfolio.require_checkpoint_funding(&expected).unwrap();
        expected.get_mut("a").unwrap().1.clear();
        assert!(portfolio.require_checkpoint_funding(&expected).is_err());
    }
    #[test]
    fn restored_settlement_receipts_prevent_double_cash_and_preserve_pending_funding() {
        let (portfolio, run, cut, limits, request) = fixture();
        portfolio.settle_simulated("a", &request, 2).unwrap();
        let pending = Reservation {
            command_id: "pending".into(),
            instrument: 1,
            cash_minor: 20,
        };
        portfolio.reserve("a", pending.clone(), 3).unwrap();
        let object = portfolio.checkpoint(&run, &cut, &limits).unwrap();
        let restored =
            Portfolio::restore_checkpoint(&run, &cut, &object, &object.id, &limits).unwrap();
        assert!(!restored.settle_simulated("a", &request, 2).unwrap());
        assert!(restored
            .reserve("a", request.reservation.clone(), 3)
            .is_err());
        let account = restored.snapshot("a").unwrap();
        assert_eq!(account.broker_available_minor, 90);
        assert_eq!(
            account.reservations,
            BTreeMap::from([("pending".into(), pending)])
        );
        assert_eq!(
            restored.checkpoint(&run, &cut, &limits).unwrap().id,
            object.id
        );
    }
    #[test]
    fn corrupt_wrong_cut_and_internally_inconsistent_images_are_rejected() {
        let (portfolio, run, cut, mut limits, request) = fixture();
        let object = portfolio.checkpoint(&run, &cut, &limits).unwrap();
        let mut corrupt = object.clone();
        corrupt.payload[0] ^= 1;
        assert!(Portfolio::restore_checkpoint(&run, &cut, &corrupt, &object.id, &limits).is_err());
        let mut padded = object.payload.clone();
        padded.push(b' ');
        let padded = Object::new(padded);
        assert!(Portfolio::restore_checkpoint(&run, &cut, &padded, &padded.id, &limits).is_err());
        assert!(
            Portfolio::restore_checkpoint(&run, &cut, &object, &"f".repeat(64), &limits).is_err()
        );
        let mut wrong = cut.clone();
        wrong.boundary_sequence += 1;
        assert!(Portfolio::restore_checkpoint(&run, &wrong, &object, &object.id, &limits).is_err());
        let mut snapshot: Snapshot = serde_json::from_slice(&object.payload).unwrap();
        snapshot
            .accounts
            .get_mut("a")
            .unwrap()
            .settlements
            .insert(request.reservation.command_id, "a".repeat(64));
        let invalid = Object::new(encode(&snapshot, limits.maximum_bytes).unwrap());
        assert!(Portfolio::restore_checkpoint(&run, &cut, &invalid, &invalid.id, &limits).is_err());
        snapshot.accounts.clear();
        let invalid = Object::new(encode(&snapshot, limits.maximum_bytes).unwrap());
        assert!(Portfolio::restore_checkpoint(&run, &cut, &invalid, &invalid.id, &limits).is_err());
        limits.maximum_bytes = 32;
        assert!(portfolio.checkpoint(&run, &cut, &limits).is_err());
        assert!(Portfolio::restore_checkpoint(&run, &cut, &object, &object.id, &limits).is_err());
    }
}

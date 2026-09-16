use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::sync::Mutex;
pub mod checkpoint;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Reservation {
    pub command_id: String,
    pub instrument: u64,
    pub cash_minor: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Account {
    pub currency: String,
    pub currency_scale: u8,
    /// None for broker-owned balances. Simulated settlement requires an exact run.
    pub simulation_run_id: Option<String>,
    pub budget_minor: u64,
    pub broker_available_minor: u64,
    pub balance_at_ns: u64,
    pub max_balance_age_ns: u64,
    pub reservations: BTreeMap<String, Reservation>,
}
/// Serializes account reservations across ticker threads; different accounts use different locks.
#[derive(Debug, Default)]
pub struct Portfolio {
    accounts: BTreeMap<String, Mutex<AccountState>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AccountState {
    account: Account,
    settlements: BTreeMap<String, String>,
}
impl std::ops::Deref for AccountState {
    type Target = Account;
    fn deref(&self) -> &Account {
        &self.account
    }
}
impl std::ops::DerefMut for AccountState {
    fn deref_mut(&mut self) -> &mut Account {
        &mut self.account
    }
}
/// The execution owner must certify terminal order state and journaled cash first.
#[derive(Debug, Clone, Serialize)]
pub struct SimulatedSettlement {
    pub run_id: String,
    pub reservation: Reservation,
    pub currency: String,
    pub currency_scale: u8,
    pub net_cash_minor: i128,
    pub at_ns: u64,
    pub evidence_hash: String,
}
impl Portfolio {
    pub fn new(accounts: BTreeMap<String, Account>) -> Result<Self> {
        if accounts.is_empty()
            || accounts.iter().any(|(id, a)| {
                id.is_empty()
                    || a.budget_minor == 0
                    || a.max_balance_age_ns == 0
                    || a.currency.len() != 3
                    || !a.currency.bytes().all(|v| v.is_ascii_uppercase())
                    || a.currency_scale > 9
                    || a.simulation_run_id
                        .as_ref()
                        .is_some_and(|id| id.is_empty() || id.len() > 128)
            })
        {
            return Err(Error::Invalid("account mandate required".into()));
        }
        Ok(Self {
            accounts: accounts
                .into_iter()
                .map(|(id, account)| {
                    (
                        id,
                        Mutex::new(AccountState {
                            account,
                            settlements: BTreeMap::new(),
                        }),
                    )
                })
                .collect(),
        })
    }
    pub fn reserve(&self, account: &str, reservation: Reservation, now_ns: u64) -> Result<()> {
        let mut a = self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?;
        if reservation.command_id.is_empty()
            || reservation.instrument == 0
            || reservation.cash_minor == 0
        {
            return Err(Error::Invalid("invalid reservation".into()));
        }
        if a.settlements.contains_key(&reservation.command_id) {
            return Err(Error::Conflict("cannot reserve a settled command".into()));
        }
        if let Some(existing) = a.reservations.get(&reservation.command_id) {
            return if existing == &reservation {
                Ok(())
            } else {
                Err(Error::Conflict("reservation command identity".into()))
            };
        }
        if a.balance_at_ns > now_ns || now_ns - a.balance_at_ns > a.max_balance_age_ns {
            return Err(Error::Unready("account balance stale".into()));
        }
        let used = a
            .reservations
            .values()
            .try_fold(0_u64, |total, r| total.checked_add(r.cash_minor))
            .ok_or_else(|| Error::Invalid("cash overflow".into()))?;
        let limit = a.budget_minor.min(a.broker_available_minor);
        if reservation.cash_minor > limit.saturating_sub(used) {
            return Err(Error::Unready("insufficient unreserved cash".into()));
        }
        a.reservations
            .insert(reservation.command_id.clone(), reservation);
        Ok(())
    }
    pub fn release(&self, account: &str, command: &str) -> Result<bool> {
        let mut a = self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?;
        Ok(a.reservations.remove(command).is_some())
    }
    /// Release only the exact reservation authorized by its execution owner.
    /// A missing reservation is not evidence that a different one may be removed.
    pub fn release_matching(&self, account: &str, expected: &Reservation) -> Result<bool> {
        let mut a = self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?;
        match a.reservations.get(&expected.command_id) {
            None => Ok(false),
            Some(actual) if actual == expected => {
                a.reservations.remove(&expected.command_id);
                Ok(true)
            }
            Some(_) => Err(Error::Conflict("reservation changed before release".into())),
        }
    }
    /// Execute a bounded, non-I/O transition while the exact reservation is held.
    /// The callback must not re-enter this account's portfolio methods.
    pub fn with_reservation<T>(
        &self,
        account: &str,
        expected: &Reservation,
        now_ns: u64,
        execute: impl FnOnce() -> Result<T>,
    ) -> Result<T> {
        let a = self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?;
        if a.reservations.get(&expected.command_id) != Some(expected) {
            return Err(Error::Unready(
                "matching cash reservation is not held".into(),
            ));
        }
        if a.balance_at_ns > now_ns || now_ns - a.balance_at_ns > a.max_balance_age_ns {
            return Err(Error::Unready(
                "account balance stale at execution transition".into(),
            ));
        }
        let total = a
            .reservations
            .values()
            .try_fold(0_u64, |sum, r| sum.checked_add(r.cash_minor))
            .ok_or_else(|| Error::Invalid("reserved cash overflow".into()))?;
        if total > a.budget_minor.min(a.broker_available_minor) {
            return Err(Error::Unready(
                "held cash exceeds current account allowance".into(),
            ));
        }
        execute()
    }
    pub fn snapshot(&self, account: &str) -> Result<Account> {
        Ok(self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?
            .account
            .clone())
    }
    pub fn require_simulation(
        &self,
        account: &str,
        run_id: &str,
        currency_scale: u8,
        currency: Option<&str>,
    ) -> Result<()> {
        let state = self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?;
        if state.simulation_run_id.as_deref() != Some(run_id)
            || state.currency_scale != currency_scale
            || currency.is_some_and(|currency| state.currency != currency)
        {
            return Err(Error::Conflict(
                "simulation account currency or run differs".into(),
            ));
        }
        Ok(())
    }
    /// In-memory atomic settlement and exact-reservation release. Never use this
    /// for broker balances. Durable publication/recovery belongs to its caller.
    pub fn settle_simulated(
        &self,
        account: &str,
        request: &SimulatedSettlement,
        maximum_receipts: usize,
    ) -> Result<bool> {
        if maximum_receipts == 0
            || request.reservation.command_id.is_empty()
            || request.reservation.instrument == 0
            || request.reservation.cash_minor == 0
            || maximum_receipts > 1_000_000
            || request.evidence_hash.len() != 64
            || !request
                .evidence_hash
                .bytes()
                .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
        {
            return Err(Error::Invalid(
                "settlement evidence or capacity invalid".into(),
            ));
        }
        let hash = crate::content_hash(&("arte.simulated-settlement.v1", account, request))?;
        let mut state = self
            .accounts
            .get(account)
            .ok_or_else(|| Error::Invalid("account not allowed".into()))?
            .lock()
            .map_err(|_| Error::Unready("account lock poisoned".into()))?;
        if state.simulation_run_id.as_deref() != Some(request.run_id.as_str())
            || state.currency != request.currency
            || state.currency_scale != request.currency_scale
        {
            return Err(Error::Conflict(
                "settlement run or currency differs from account".into(),
            ));
        }
        if let Some(previous) = state.settlements.get(&request.reservation.command_id) {
            return if previous == &hash {
                Ok(false)
            } else {
                Err(Error::Conflict("settlement request changed".into()))
            };
        }
        if state.settlements.len() >= maximum_receipts {
            return Err(Error::Capacity("settlement receipt capacity".into()));
        }
        if state.reservations.get(&request.reservation.command_id) != Some(&request.reservation) {
            return Err(Error::Unready(
                "settlement reservation missing or changed".into(),
            ));
        }
        if request.at_ns < state.balance_at_ns {
            return Err(Error::Conflict("settlement predates account cash".into()));
        }
        let available = i128::from(state.broker_available_minor)
            .checked_add(request.net_cash_minor)
            .and_then(|v| u64::try_from(v).ok())
            .ok_or_else(|| {
                Error::Capacity("settlement cash outside unsigned account range".into())
            })?;
        // Do not change the capital mandate or fabricate a fresh broker balance.
        state.account.broker_available_minor = available;
        state
            .account
            .reservations
            .remove(&request.reservation.command_id);
        state
            .settlements
            .insert(request.reservation.command_id.clone(), hash);
        Ok(true)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn settlement_is_atomic_idempotent_and_rejects_broker_balances() {
        let reservation = Reservation {
            command_id: "c".into(),
            instrument: 1,
            cash_minor: 50,
        };
        let account = Account {
            currency: "USD".into(),
            currency_scale: 2,
            simulation_run_id: Some("r".into()),
            budget_minor: 100,
            broker_available_minor: 100,
            balance_at_ns: 1,
            max_balance_age_ns: 100,
            reservations: BTreeMap::from([("c".into(), reservation.clone())]),
        };
        let p = Portfolio::new(BTreeMap::from([("a".into(), account.clone())])).unwrap();
        let request = SimulatedSettlement {
            run_id: "r".into(),
            reservation,
            currency: "USD".into(),
            currency_scale: 2,
            net_cash_minor: -10,
            at_ns: 2,
            evidence_hash: "a".repeat(64),
        };
        let mut invalid = request.clone();
        invalid.net_cash_minor = -101;
        assert!(p.settle_simulated("a", &invalid, 1).is_err());
        invalid = request.clone();
        invalid.currency = "CAD".into();
        assert!(p.settle_simulated("a", &invalid, 1).is_err());
        assert_eq!(p.snapshot("a").unwrap().broker_available_minor, 100);
        assert_eq!(p.snapshot("a").unwrap().reservations.len(), 1);
        let successes = std::thread::scope(|threads| {
            let handles: Vec<_> = (0..8)
                .map(|_| {
                    let p = &p;
                    let request = &request;
                    threads.spawn(move || p.settle_simulated("a", request, 1).unwrap())
                })
                .collect();
            handles
                .into_iter()
                .map(|h| usize::from(h.join().unwrap()))
                .sum::<usize>()
        });
        assert_eq!(successes, 1);
        let state = p.snapshot("a").unwrap();
        assert_eq!(
            (
                state.broker_available_minor,
                state.budget_minor,
                state.balance_at_ns
            ),
            (90, 100, 1)
        );
        assert!(state.reservations.is_empty());
        assert!(p.reserve("a", request.reservation.clone(), 2).is_err());
        assert!(p
            .require_simulation("a", "another", 2, Some("USD"))
            .is_err());
        assert!(p.require_simulation("a", "r", 2, Some("CAD")).is_err());
        p.require_simulation("a", "r", 2, Some("USD")).unwrap();
        let mut changed = request.clone();
        changed.net_cash_minor += 1;
        assert!(p.settle_simulated("a", &changed, 1).is_err());
        let mut broker = account;
        broker.simulation_run_id = None;
        let broker = Portfolio::new(BTreeMap::from([("a".into(), broker)])).unwrap();
        assert!(broker.settle_simulated("a", &request, 1).is_err());
        let second = Reservation {
            command_id: "second".into(),
            instrument: 1,
            cash_minor: 20,
        };
        p.reserve("a", second.clone(), 2).unwrap();
        let second = SimulatedSettlement {
            reservation: second,
            ..request
        };
        assert!(p.settle_simulated("a", &second, 1).is_err());
        assert_eq!(p.snapshot("a").unwrap().reservations.len(), 1);
    }
    #[test]
    fn matching_release_rejects_changed_cash_and_preserves_other_reservations() {
        let original = Reservation {
            command_id: "one".into(),
            instrument: 1,
            cash_minor: 40,
        };
        let other = Reservation {
            command_id: "two".into(),
            instrument: 2,
            cash_minor: 30,
        };
        let p = Portfolio::new(BTreeMap::from([(
            "a".into(),
            Account {
                currency: "USD".into(),
                currency_scale: 2,
                simulation_run_id: Some("r".into()),
                budget_minor: 100,
                broker_available_minor: 100,
                balance_at_ns: 1,
                max_balance_age_ns: 100,
                reservations: BTreeMap::from([
                    ("one".into(), original.clone()),
                    ("two".into(), other.clone()),
                ]),
            },
        )]))
        .unwrap();
        let mut changed = original.clone();
        changed.cash_minor += 1;
        assert!(p.release_matching("a", &changed).is_err());
        assert_eq!(p.snapshot("a").unwrap().reservations.len(), 2);
        assert!(p.release_matching("a", &original).unwrap());
        assert!(!p.release_matching("a", &original).unwrap());
        let account = p.snapshot("a").unwrap();
        assert_eq!(
            account.reservations,
            BTreeMap::from([("two".into(), other)])
        );
        assert_eq!(account.broker_available_minor, 100);
    }
    #[test]
    fn concurrent_tickers_cannot_overspend() {
        let p = Portfolio::new(BTreeMap::from([(
            "a".into(),
            Account {
                currency: "USD".into(),
                currency_scale: 2,
                simulation_run_id: Some("r".into()),
                budget_minor: 100,
                broker_available_minor: 100,
                balance_at_ns: 1,
                max_balance_age_ns: 100,
                reservations: BTreeMap::new(),
            },
        )]))
        .unwrap();
        let successes = std::thread::scope(|scope| {
            let handles: Vec<_> = (1..=8)
                .map(|i| {
                    let p = &p;
                    scope.spawn(move || {
                        p.reserve(
                            "a",
                            Reservation {
                                command_id: i.to_string(),
                                instrument: i,
                                cash_minor: 60,
                            },
                            2,
                        )
                        .is_ok()
                    })
                })
                .collect();
            handles
                .into_iter()
                .filter_map(|h| h.join().ok())
                .filter(|v| *v)
                .count()
        });
        assert_eq!(successes, 1);
    }
}

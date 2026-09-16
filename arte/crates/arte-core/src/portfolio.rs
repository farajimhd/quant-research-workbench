use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::sync::Mutex;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Reservation {
    pub command_id: String,
    pub instrument: u64,
    pub cash_minor: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Account {
    pub budget_minor: u64,
    pub broker_available_minor: u64,
    pub balance_at_ns: u64,
    pub max_balance_age_ns: u64,
    pub reservations: BTreeMap<String, Reservation>,
}
/// Serializes account reservations across ticker threads; different accounts use different locks.
#[derive(Debug, Default)]
pub struct Portfolio {
    accounts: BTreeMap<String, Mutex<Account>>,
}
impl Portfolio {
    pub fn new(accounts: BTreeMap<String, Account>) -> Result<Self> {
        if accounts.is_empty()
            || accounts
                .iter()
                .any(|(id, a)| id.is_empty() || a.budget_minor == 0 || a.max_balance_age_ns == 0)
        {
            return Err(Error::Invalid("account mandate required".into()));
        }
        Ok(Self {
            accounts: accounts
                .into_iter()
                .map(|(id, a)| (id, Mutex::new(a)))
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
            .clone())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
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

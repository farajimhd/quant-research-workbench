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

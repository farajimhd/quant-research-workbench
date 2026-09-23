//! Immutable ClickHouse snapshots of the live/historical Early Squeeze state.
//! A snapshot is computation recovery, never broker or trading permission.
use super::{
    portfolio_checkpoints::{from_hex, to_hex},
    ClickHouse,
};
use arte_core::{
    config::Acceptance,
    seed_storage::Object,
    strategy350_signal::{Config, Mode, State},
    Error, Result,
};
use serde_json::Value;
use std::{collections::BTreeSet, future::Future};

const TABLE: &str = "signal_checkpoints_v1";
const MAX_BYTES: usize = 4096;

fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn signal_checkpoint_scope(scope_hash: &str) -> Result<String> {
    if !hash_valid(scope_hash) {
        return Err(Error::Invalid("signal checkpoint scope".into()));
    }
    arte_core::content_hash(&("arte.signal-checkpoint-owner.v1", scope_hash))
}
pub struct Recovery<'a> {
    pub scope_hash: &'a str,
    pub config: Config,
    pub session_start_ns: u64,
    pub session_end_ns: u64,
    pub mode: Mode,
    pub maximum_next_ns: u64,
    pub as_of_ns: u64,
}
impl Recovery<'_> {
    fn validate(&self) -> Result<()> {
        signal_checkpoint_scope(self.scope_hash)?;
        self.config.hash()?;
        if self.session_start_ns == 0
            || self.session_start_ns >= self.session_end_ns
            || self.maximum_next_ns < self.session_start_ns
            || self.maximum_next_ns > self.session_end_ns
            || self.as_of_ns < self.session_start_ns
        {
            return Err(Error::Invalid("signal checkpoint recovery bounds".into()));
        }
        Ok(())
    }
}
#[derive(Clone, PartialEq, Eq)]
struct Row {
    payload: Vec<u8>,
    published_at_ns: u64,
}
fn number(value: &Value, name: &str) -> Result<u64> {
    value
        .get(name)
        .and_then(|v| {
            v.as_u64()
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        })
        .ok_or_else(|| Error::Invalid(format!("signal checkpoint {name}")))
}
fn decode_slot(body: &str, next_bucket_ns: u64) -> Result<Option<Row>> {
    let mut found = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        let value: Value =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        let published_at_ns = number(&value, "published_at_ns")?;
        if published_at_ns < next_bucket_ns {
            return Err(Error::Conflict(
                "signal checkpoint publication predates prefix".into(),
            ));
        }
        let hex = value
            .get("payload_hex")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("signal checkpoint payload".into()))?;
        let row = Row {
            payload: from_hex(hex, MAX_BYTES)?,
            published_at_ns,
        };
        if found.as_ref().is_some_and(|old| old != &row) {
            return Err(Error::Conflict(
                "signal checkpoint slot has conflicting rows".into(),
            ));
        }
        found = Some(row);
    }
    Ok(found)
}
trait Store {
    fn slot(&self, scope: &str, next: u64) -> impl Future<Output = Result<Option<Row>>>;
    fn latest(
        &self,
        scope: &str,
        maximum_next: u64,
        as_of: u64,
    ) -> impl Future<Output = Result<Option<u64>>>;
    fn write(&self, scope: &str, next: u64, row: &Row) -> impl Future<Output = Result<()>>;
}
impl Store for ClickHouse {
    async fn slot(&self, scope: &str, next: u64) -> Result<Option<Row>> {
        signal_checkpoint_scope(scope)?;
        self.verify_storage(TABLE).await?;
        let sql = format!(
            "SELECT DISTINCT payload_hex,published_at_ns FROM {}.{TABLE} WHERE scope_hash='{scope}' AND next_bucket_ns={next} LIMIT 3 FORMAT JSONEachRow",
            self.database,
        );
        decode_slot(&self.request(&sql, String::new()).await?, next)
    }
    async fn latest(&self, scope: &str, maximum_next: u64, as_of: u64) -> Result<Option<u64>> {
        signal_checkpoint_scope(scope)?;
        self.verify_storage(TABLE).await?;
        let sql = format!(
            "SELECT next_bucket_ns FROM {}.{TABLE} WHERE scope_hash='{scope}' AND next_bucket_ns<={maximum_next} AND published_at_ns<={as_of} ORDER BY next_bucket_ns DESC LIMIT 1 FORMAT JSONEachRow",
            self.database,
        );
        let body = self.request(&sql, String::new()).await?;
        let mut lines = body.lines().filter(|line| !line.trim().is_empty());
        let next = lines
            .next()
            .map(|line| {
                let value: Value =
                    serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
                number(&value, "next_bucket_ns")
            })
            .transpose()?;
        if lines.next().is_some() || next.is_some_and(|at| at > maximum_next) {
            return Err(Error::Conflict("signal checkpoint latest cursor".into()));
        }
        Ok(next)
    }
    async fn write(&self, scope: &str, next: u64, row: &Row) -> Result<()> {
        signal_checkpoint_scope(scope)?;
        self.insert(
            TABLE,
            &[serde_json::json!({
                "scope_hash": scope, "next_bucket_ns": next,
                "payload_hex": to_hex(&row.payload), "published_at_ns": row.published_at_ns,
            })],
        )
        .await
    }
}
async fn load_exact(store: &impl Store, request: &Recovery<'_>, next: u64) -> Result<State> {
    request.validate()?;
    if next < request.session_start_ns || next > request.maximum_next_ns {
        return Err(Error::Invalid("signal checkpoint requested prefix".into()));
    }
    let row = store
        .slot(request.scope_hash, next)
        .await?
        .ok_or_else(|| Error::Unready("signal checkpoint missing".into()))?;
    if row.published_at_ns > request.as_of_ns {
        return Err(Error::Unready("signal checkpoint not yet published".into()));
    }
    let object = Object::new(row.payload);
    let state = State::restore(
        &object,
        request.scope_hash,
        request.config.clone(),
        request.session_start_ns,
        request.session_end_ns,
        request.mode,
    )?;
    if state.next_bucket_ns() != next
        || state
            .last_available_at_ns()
            .is_some_and(|at| at > row.published_at_ns)
    {
        return Err(Error::Conflict(
            "signal checkpoint prefix or publication clock".into(),
        ));
    }
    Ok(state)
}
async fn load_latest(store: &impl Store, request: &Recovery<'_>) -> Result<State> {
    request.validate()?;
    let next = store
        .latest(
            request.scope_hash,
            request.maximum_next_ns,
            request.as_of_ns,
        )
        .await?
        .ok_or_else(|| Error::Unready("no signal checkpoint as of cutoff".into()))?;
    load_exact(store, request, next).await
}
async fn publish(
    store: &impl Store,
    state: &State,
    request: &Recovery<'_>,
    published_at_ns: u64,
    owned: &impl Fn() -> Result<()>,
) -> Result<String> {
    request.validate()?;
    if state.scope_hash() != request.scope_hash
        || state.mode() != request.mode
        || state.next_bucket_ns() > request.maximum_next_ns
        || published_at_ns > request.as_of_ns
        || published_at_ns < state.next_bucket_ns()
        || state
            .last_available_at_ns()
            .is_some_and(|at| at > published_at_ns)
    {
        return Err(Error::Conflict(
            "signal checkpoint publication context".into(),
        ));
    }
    let object = state.checkpoint()?;
    State::restore(
        &object,
        request.scope_hash,
        request.config.clone(),
        request.session_start_ns,
        request.session_end_ns,
        request.mode,
    )?;
    let next = state.next_bucket_ns();
    owned()?;
    let existing = store.slot(request.scope_hash, next).await?;
    let row = match existing {
        Some(row) if row.payload == object.payload && row.published_at_ns <= published_at_ns => row,
        Some(_) => {
            return Err(Error::Conflict(
                "signal checkpoint slot already differs".into(),
            ))
        }
        None => {
            let row = Row {
                payload: object.payload,
                published_at_ns,
            };
            owned()?;
            store.write(request.scope_hash, next, &row).await?;
            row
        }
    };
    owned()?;
    if store.slot(request.scope_hash, next).await?.as_ref() != Some(&row) {
        return Err(Error::Unready("signal checkpoint readback differs".into()));
    }
    load_exact(store, request, next).await?;
    owned()?;
    Ok(object.id)
}
impl ClickHouse {
    pub async fn load_latest_signal_checkpoint(&self, request: &Recovery<'_>) -> Result<State> {
        load_latest(self, request).await
    }
    pub async fn publish_signal_checkpoint(
        &self,
        state: &State,
        request: &Recovery<'_>,
        published_at_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "signal publication acceptance missing: {required:?}"
                )));
            }
        }
        let scope = signal_checkpoint_scope(request.scope_hash)?;
        lease.require(&scope)?;
        publish(self, state, request, published_at_ns, &|| {
            lease.require(&scope)
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        cell::{Cell, RefCell},
        collections::BTreeMap,
    };
    const S: u64 = 1_000_000_000;
    #[derive(Default)]
    struct Memory {
        slots: RefCell<BTreeMap<(String, u64), Vec<Row>>>,
        fail_after_write: Cell<bool>,
    }
    impl Store for Memory {
        async fn slot(&self, scope: &str, next: u64) -> Result<Option<Row>> {
            let rows = self.slots.borrow();
            let Some(values) = rows.get(&(scope.into(), next)) else {
                return Ok(None);
            };
            if values.iter().any(|v| v != &values[0]) {
                return Err(Error::Conflict("mock conflicting checkpoint rows".into()));
            }
            Ok(values.first().cloned())
        }
        async fn latest(&self, scope: &str, maximum_next: u64, as_of: u64) -> Result<Option<u64>> {
            Ok(self
                .slots
                .borrow()
                .iter()
                .filter(|((key, next), rows)| {
                    key == scope
                        && *next <= maximum_next
                        && rows.iter().any(|r| r.published_at_ns <= as_of)
                })
                .map(|((_, next), _)| *next)
                .max())
        }
        async fn write(&self, scope: &str, next: u64, row: &Row) -> Result<()> {
            self.slots
                .borrow_mut()
                .entry((scope.into(), next))
                .or_default()
                .push(row.clone());
            if self.fail_after_write.replace(false) {
                return Err(Error::Unready("ambiguous checkpoint insert".into()));
            }
            Ok(())
        }
    }
    fn config() -> Config {
        Config {
            minimum_move_bps: 5,
            source_algorithm_hash: "b".repeat(64),
        }
    }
    fn request<'a>(scope: &'a str, as_of: u64) -> Recovery<'a> {
        Recovery {
            scope_hash: scope,
            config: config(),
            session_start_ns: S,
            session_end_ns: S + 300_000_000,
            mode: Mode::Live,
            maximum_next_ns: S + 300_000_000,
            as_of_ns: as_of,
        }
    }
    #[tokio::test]
    async fn latest_checkpoint_is_causal_and_ambiguous_retry_is_idempotent() {
        let scope = "a".repeat(64);
        let store = Memory::default();
        let mut state = State::new_live(config(), scope.clone(), S, S + 300_000_000).unwrap();
        state
            .observe_live(S, true, 10_000, 100, 2, S + 100_000_000)
            .unwrap();
        let at = S + 150_000_000;
        store.fail_after_write.set(true);
        assert!(
            publish(&store, &state, &request(&scope, at), at, &|| Ok(()))
                .await
                .is_err()
        );
        let id = publish(&store, &state, &request(&scope, at + 1), at + 1, &|| Ok(()))
            .await
            .unwrap();
        assert_eq!(
            store.slots.borrow().values().map(Vec::len).sum::<usize>(),
            1
        );
        assert_eq!(
            load_latest(&store, &request(&scope, at))
                .await
                .unwrap()
                .checkpoint()
                .unwrap()
                .id,
            id
        );
        assert!(load_latest(&store, &request(&scope, at - 1)).await.is_err());
        state
            .observe_live(S + 100_000_000, true, 10_005, 101, 3, S + 220_000_000)
            .unwrap();
        let later = S + 230_000_000;
        publish(&store, &state, &request(&scope, later), later, &|| Ok(()))
            .await
            .unwrap();
        assert_eq!(
            load_latest(&store, &request(&scope, at))
                .await
                .unwrap()
                .next_bucket_ns(),
            S + 100_000_000
        );
        assert_eq!(
            load_latest(&store, &request(&scope, later))
                .await
                .unwrap()
                .next_bucket_ns(),
            S + 200_000_000
        );
        assert!(load_latest(&store, &request(&"c".repeat(64), later))
            .await
            .is_err());
    }
    #[tokio::test]
    async fn conflicting_slot_and_invalid_publication_fail_closed() {
        let scope = "a".repeat(64);
        let store = Memory::default();
        let mut state = State::new_live(config(), scope.clone(), S, S + 300_000_000).unwrap();
        state
            .observe_live(S, true, 10_000, 100, 2, S + 100_000_000)
            .unwrap();
        let at = S + 150_000_000;
        assert!(publish(
            &store,
            &state,
            &request(&scope, at),
            S + 99_000_000,
            &|| Ok(())
        )
        .await
        .is_err());
        assert!(publish(&store, &state, &request(&scope, at), at, &|| Err(
            Error::Unready("lost lease".into())
        ))
        .await
        .is_err());
        publish(&store, &state, &request(&scope, at), at, &|| Ok(()))
            .await
            .unwrap();
        let mut other = State::new_live(config(), scope.clone(), S, S + 300_000_000).unwrap();
        other
            .observe_live(S, true, 11_000, 100, 2, S + 100_000_000)
            .unwrap();
        assert!(
            publish(&store, &other, &request(&scope, at + 1), at + 1, &|| Ok(()))
                .await
                .is_err()
        );
        store
            .slots
            .borrow_mut()
            .get_mut(&(scope.clone(), S + 100_000_000))
            .unwrap()
            .push(Row {
                payload: other.checkpoint().unwrap().payload,
                published_at_ns: at,
            });
        assert!(load_latest(&store, &request(&scope, at)).await.is_err());
        let body = format!(
            "{}\n{}",
            serde_json::json!({"payload_hex":"00","published_at_ns":at}),
            serde_json::json!({"payload_hex":"01","published_at_ns":at})
        );
        assert!(decode_slot(&body, S + 100_000_000).is_err());
    }
}

//! Feature state is bound to both the exact market image and observed boundary.
use super::*;
use crate::seed_storage::Object;
use std::io::Write;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    encounters: Object,
    version: u32,
    context: String,
    configuration: String,
    market: String,
    scope: (u16, u64, u32),
    macd: Macd,
    setup: SetupState,
    activity: ActivityState,
    snapshot: Snapshot,
}
fn bounds(context: &str, maximum: usize) -> Result<()> {
    if context.len() != 64
        || !context
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || maximum == 0
        || maximum > 64 * 1024 * 1024
    {
        return Err(Error::Invalid("feature recovery context or budget".into()));
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
            return Err(std::io::Error::other("feature recovery byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl State {
    fn require_recovery_boundary(&self, market: &Runtime, boundary: &Boundary<'_>) -> Result<()> {
        let snapshot = self
            .snapshot()?
            .ok_or_else(|| Error::Unready("features have not observed a boundary".into()))?;
        let input = boundary.input(String::new());
        if market.source_scope() != self.scope
            || market.configuration_hash() != self.market_hash
            || snapshot.boundary_id != boundary.id
            || snapshot.sequence != boundary.sequence
            || snapshot.available_at_ns != input.available_at_ns
            || snapshot.evaluated_at_ns != boundary.evaluated_at_ns
            || snapshot
                .macd
                .as_ref()
                .is_some_and(|m| m.at_ns > boundary.evaluated_at_ns)
        {
            return Err(Error::Conflict("feature recovery boundary differs".into()));
        }
        match (&snapshot.one_second, &boundary.kind) {
            (
                Some(one),
                Kind::Completed {
                    interval_ns: SECOND,
                    bar,
                    ..
                },
            ) if one.at_ns == bar.bar.end_ns
                && one.vwap == bar.session_vwap
                && one.high == bar.session_high
                && one.prior_high == bar.prior_session_high
                && one.levels.len() <= self.config.maximum_levels => {}
            (
                None,
                Kind::Completed {
                    interval_ns: SECOND,
                    ..
                },
            )
            | (Some(_), _) => {
                return Err(Error::Conflict("feature recovery candle differs".into()))
            }
            (None, _) => {}
        }
        Ok(())
    }
    /// Capture after observing the pending boundary. Genesis is recreated from
    /// configuration, not restored as an observed feature checkpoint.
    pub fn checkpoint(
        &self,
        context: &str,
        market: &Runtime,
        boundary: &Boundary<'_>,
        maximum_bytes: usize,
    ) -> Result<Object> {
        bounds(context, maximum_bytes)?;
        self.require_recovery_boundary(market, boundary)?;
        let saved = Saved {
            encounters: self
                .encounters
                .checkpoint(context, market, boundary, maximum_bytes)?,
            version: 2,
            context: context.into(),
            configuration: self.config_hash.clone(),
            market: market.checkpoint()?.hash,
            scope: (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            macd: self.macd.clone(),
            setup: self.setup.clone(),
            activity: self.activity.clone(),
            snapshot: self.snapshot.as_ref().unwrap().clone(),
        };
        let mut writer = Writer {
            bytes: Vec::new(),
            maximum: maximum_bytes,
        };
        serde_json::to_writer(&mut writer, &saved)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Object::new(writer.bytes))
    }
    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        image: &Object,
        expected_hash: &str,
        context: &str,
        market: &Runtime,
        config: Config,
        boundary: &Boundary<'_>,
        maximum_bytes: usize,
    ) -> Result<Self> {
        bounds(context, maximum_bytes)?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Invalid("feature recovery identity or bytes".into()));
        }
        image.verify()?;
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut state = Self::new(market, config)?;
        if saved.version != 2
            || saved.context != context
            || saved.configuration != state.config_hash
            || saved.scope
                != (
                    state.scope.provider,
                    state.scope.instrument,
                    state.scope.session,
                )
            || saved.market != market.checkpoint()?.hash
        {
            return Err(Error::Conflict("feature recovery pins differ".into()));
        }
        state.macd = saved.macd;
        state.encounters = crate::strategy_encounters::stream::Runtime::restore_checkpoint(
            &saved.encounters,
            &saved.encounters.id,
            context,
            market,
            state.config.encounters.clone(),
            boundary,
            maximum_bytes,
        )?;
        if crate::content_hash(&saved.snapshot.encounters)?
            != crate::content_hash(state.encounters.snapshot()?.unwrap())?
        {
            return Err(Error::Conflict("feature encounter snapshot differs".into()));
        }
        state.setup = saved.setup;
        state.activity = saved.activity;
        state.snapshot = Some(saved.snapshot);
        state.require_recovery_boundary(market, boundary)?;
        if state
            .checkpoint(context, market, boundary, maximum_bytes)?
            .payload
            != image.payload
        {
            return Err(Error::Conflict(
                "noncanonical feature recovery image".into(),
            ));
        }
        Ok(state)
    }
}

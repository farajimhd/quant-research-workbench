//! Streaming recovery only. These objects never become historical V7 seeds.
use super::*;
use crate::seed_storage::Object;
use std::io::Write;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    context: String,
    configuration: String,
    market: String,
    state: State,
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
        return Err(Error::Invalid(
            "encounter recovery context or budget".into(),
        ));
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
            return Err(std::io::Error::other("encounter recovery byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl Runtime {
    fn require_recovery(
        &self,
        market: &market_structure::Runtime,
        boundary: &Boundary<'_>,
    ) -> Result<()> {
        market.market()?;
        let snapshot = self
            .snapshot()?
            .ok_or_else(|| Error::Unready("encounter boundary not observed".into()))?;
        if market.source_scope() != self.scope
            || market.configuration_hash() != self.market_hash
            || snapshot.boundary_id != boundary.id
            || snapshot.sequence != boundary.sequence
            || snapshot.evaluated_at_ns != boundary.evaluated_at_ns
            || snapshot.input_hash != boundary_hash(boundary)?
            || snapshot.tracked_levels != self.state.levels.len()
            || snapshot.blocked != self.state.blocked()
            || snapshot.exit_reason != self.state.exit_reason
            || self.state.levels.len() > self.config.settings.maximum_encounters
            || self.state.at_ns > boundary.evaluated_at_ns
            || (self.state.session != self.scope.session
                && !(self.state.session == 0
                    && self.state.at_ns == 0
                    && self.state.levels.is_empty()
                    && self.state.exit_reason.is_none()))
        {
            return Err(Error::Conflict(
                "encounter recovery boundary or state differs".into(),
            ));
        }
        for (id, e) in &self.state.levels {
            let l = &e.level;
            let threshold = l.price
                + (self.config.tick * self.config.settings.breakout_buffer_ticks)
                    .max(l.price * self.config.settings.breakout_buffer_bps / 10000.);
            let failure = l.lower * (1. - self.config.settings.rejection_break_offset_bps / 10000.);
            if id.is_empty()
                || id != &l.id
                || [l.price, l.lower, l.upper, e.threshold, e.failure_threshold]
                    .iter()
                    .any(|p| !p.is_finite() || *p <= 0.)
                || l.lower > l.price
                || l.price > l.upper
                || l.confirmed_at_ns > self.state.at_ns
                || threshold != e.threshold
                || failure != e.failure_threshold
                || !matches!(
                    l.role,
                    crate::v7_encounters::ActiveRole::Resistance
                        | crate::v7_encounters::ActiveRole::Transition
                )
                || [e.failed_at_ns, e.recovered_at_ns, e.touch_at_ns]
                    .iter()
                    .flatten()
                    .any(|at| *at > boundary.evaluated_at_ns)
                || (e.status == super::super::Status::Failed && e.failed_at_ns.is_none())
                || (e.status == super::super::Status::Warning && e.warning.is_none())
                || e.warning.as_ref().is_some_and(|w| {
                    !w.open.is_finite()
                        || w.open <= 0.
                        || !w.fraction.is_finite()
                        || !(0. ..=1.).contains(&w.fraction)
                        || w.end_ns > self.state.at_ns
                })
            {
                return Err(Error::Invalid(
                    "encounter recovery geometry or causal state".into(),
                ));
            }
        }
        Ok(())
    }
    /// Capture an observed pending boundary. Genesis is recreated, not restored.
    pub fn checkpoint(
        &self,
        context: &str,
        market: &market_structure::Runtime,
        boundary: &Boundary<'_>,
        maximum_bytes: usize,
    ) -> Result<Object> {
        bounds(context, maximum_bytes)?;
        self.require_recovery(market, boundary)?;
        let saved = Saved {
            version: 1,
            context: context.into(),
            configuration: self.configuration_hash.clone(),
            market: market.checkpoint()?.hash,
            state: self.state.clone(),
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
        market: &market_structure::Runtime,
        config: Config,
        boundary: &Boundary<'_>,
        maximum_bytes: usize,
    ) -> Result<Self> {
        bounds(context, maximum_bytes)?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Invalid(
                "encounter recovery identity or bytes".into(),
            ));
        }
        image.verify()?;
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut runtime = Self::new(market, config)?;
        if saved.version != 1
            || saved.context != context
            || saved.configuration != runtime.configuration_hash
            || saved.market != market.checkpoint()?.hash
        {
            return Err(Error::Conflict("encounter recovery pins differ".into()));
        }
        runtime.state = saved.state;
        runtime.snapshot = Some(saved.snapshot);
        runtime.require_recovery(market, boundary)?;
        if runtime
            .checkpoint(context, market, boundary, maximum_bytes)?
            .payload
            != image.payload
        {
            return Err(Error::Conflict(
                "noncanonical encounter recovery image".into(),
            ));
        }
        Ok(runtime)
    }
}

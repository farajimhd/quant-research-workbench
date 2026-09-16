//! Immutable session-local recovery. Never an authoritative historical seed.
use super::*;
use crate::seed_storage::Object;
use std::io::Write;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    context: String,
    configuration: String,
    state: State,
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
            "local swing recovery context or budget".into(),
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
            return Err(std::io::Error::other("local swing recovery byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl State {
    fn validate_recovery(&self) -> Result<()> {
        self.snapshot()?;
        self.config.validate()?;
        if self.sequence == 0 {
            if content_hash(self)?
                != content_hash(&Self::new(
                    self.instrument,
                    self.session,
                    self.config.clone(),
                )?)?
            {
                return Err(Error::Invalid("local swing genesis differs".into()));
            }
            return Ok(());
        }
        let previous = self
            .previous
            .as_ref()
            .ok_or_else(|| Error::Invalid("local swing previous bar missing".into()))?;
        let snapshot = self
            .snapshot
            .as_ref()
            .ok_or_else(|| Error::Invalid("local swing snapshot missing".into()))?;
        let expected_end = self
            .sequence
            .checked_sub(1)
            .and_then(|n| n.checked_mul(1_000_000_000))
            .and_then(|n| self.generation.checked_add(n));
        if self.generation == 0
            || expected_end != Some(previous.end_ns)
            || previous.start_ns.checked_add(1_000_000_000) != Some(previous.end_ns)
            || [previous.open, previous.high, previous.low, previous.close]
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.)
            || previous.low > previous.open.min(previous.close)
            || previous.high < previous.open.max(previous.close)
            || snapshot.at_ns != previous.end_ns
            || snapshot.swings.len() > self.config.maximum_levels * 2
            || self.levels.len() > self.config.maximum_levels
            || !(-1..=1).contains(&self.direction)
            || self.ranges.len() != self.sequence.min(30) as usize
            || self.ranges.iter().any(|v| !v.is_finite() || *v < 0.)
            || (snapshot.gap_reset && self.sequence != 1)
        {
            return Err(Error::Invalid(
                "local swing recovery clocks or bounds".into(),
            ));
        }
        let clock = |at: u64| {
            at >= self.generation
                && at <= previous.end_ns
                && (at - self.generation).is_multiple_of(1_000_000_000)
        };
        for extreme in [&self.high, &self.low] {
            let e = extreme
                .as_ref()
                .ok_or_else(|| Error::Invalid("local swing extreme missing".into()))?;
            if !e.price.is_finite()
                || e.price <= 0.
                || !e.distance.is_finite()
                || e.distance <= 0.
                || !clock(e.at_ns)
            {
                return Err(Error::Invalid("local swing recovery extreme".into()));
            }
        }
        let swing_valid = |s: &Swing| {
            s.id.len() == 64
                && s.id
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                && [s.lower, s.price, s.upper].iter().all(|v| v.is_finite())
                && s.price > 0.
                && s.lower <= s.price
                && s.price <= s.upper
                && clock(s.pivot_at_ns)
                && clock(s.confirmed_at_ns)
                && s.pivot_at_ns < s.confirmed_at_ns
        };
        if snapshot.swings.iter().any(|s| !swing_valid(s) || !s.active) {
            return Err(Error::Invalid("local swing recovery projection".into()));
        }
        for (key, l) in &self.levels {
            if *key == 0
                || *key > self.next_id
                || !swing_valid(&l.swing)
                || l.swing.id
                    != content_hash(&(
                        VERSION,
                        self.instrument,
                        self.session,
                        self.generation,
                        key,
                    ))?
                || l.swing.active != (l.phase == Phase::Active)
                || l.beyond > 2
                || l.last_test == 0
                || l.last_test > self.sequence
                || self.sequence - l.last_test >= self.config.lifetime_bars
                || l.break_at > self.sequence
                || l.contact_at > self.sequence
                || (l.phase != Phase::Active && (l.break_at == 0 || l.beyond != 2))
                || (l.phase == Phase::RetestContact && l.contact_at <= l.break_at)
            {
                return Err(Error::Invalid("local swing recovery level state".into()));
            }
        }
        Ok(())
    }
    pub fn checkpoint(&self, context: &str, maximum_bytes: usize) -> Result<Object> {
        bounds(context, maximum_bytes)?;
        self.validate_recovery()?;
        let saved = Saved {
            version: 1,
            context: context.into(),
            configuration: self.configuration_hash()?,
            state: self.clone(),
        };
        let mut writer = Writer {
            bytes: Vec::new(),
            maximum: maximum_bytes,
        };
        serde_json::to_writer(&mut writer, &saved)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Object::new(writer.bytes))
    }
    /// Caller supplies the independent scope, policy and last consumed candle.
    /// `None` permits genesis only, not a missing market checkpoint.
    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        image: &Object,
        expected_hash: &str,
        context: &str,
        instrument: u64,
        session: u32,
        config: Config,
        previous: Option<&Bar>,
        maximum_bytes: usize,
    ) -> Result<Self> {
        bounds(context, maximum_bytes)?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Invalid(
                "local swing recovery identity or bytes".into(),
            ));
        }
        image.verify()?;
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let expected = Self::new(instrument, session, config)?.configuration_hash()?;
        if saved.version != 1
            || saved.context != context
            || saved.configuration != expected
            || saved.state.configuration_hash()? != expected
            || saved.state.previous.as_ref() != previous
        {
            return Err(Error::Conflict("local swing recovery pins differ".into()));
        }
        saved.state.validate_recovery()?;
        if saved.state.checkpoint(context, maximum_bytes)?.payload != image.payload {
            return Err(Error::Conflict(
                "noncanonical local swing recovery image".into(),
            ));
        }
        Ok(saved.state)
    }
}

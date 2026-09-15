//! Official regular-session band admission. No estimates or refreshed timestamps.
use crate::{event_order::Scope, events::Decimal, Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Evidence {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub lower: i64,
    pub upper: i64,
    pub scale: u8,
    pub effective_at_ns: u64,
    pub available_at_ns: u64,
    pub official: bool,
}
impl Evidence {
    pub fn require(&self, scope: Scope, scale: u8, now_ns: u64, maximum_age_ns: u64) -> Result<()> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || scale > 9
            || maximum_age_ns == 0
            || !self.official
            || self.provider != scope.provider
            || self.instrument != scope.instrument
            || self.session != scope.session
            || self.scale != scale
            || self.lower <= 0
            || self.upper <= self.lower
            || self.effective_at_ns > self.available_at_ns
            || self.available_at_ns > now_ns
            || now_ns - self.effective_at_ns > maximum_age_ns
        {
            return Err(Error::Unready(
                "official LULD scope, clocks or geometry invalid".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Policy {
    pub tick: i64,
    pub scale: u8,
    pub buffer_ticks: u32,
    pub buffer_bps: Decimal,
    pub include_spread: bool,
    pub maximum_age_ns: u64,
    pub minimum_previous_close: i64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Buffered {
    pub lower_exit: i64,
    pub target: i64,
    pub scale: u8,
    pub effective_at_ns: u64,
    pub available_at_ns: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Block {
    RegularPreviousCloseUnavailable,
    RegularPreviousCloseBelowMinimum,
    OfficialLuldUnavailable,
    InsideLuldBuffer,
}
impl Block {
    pub fn reason(self) -> &'static str {
        match self {
            Self::RegularPreviousCloseUnavailable => "regular_previous_close_unavailable",
            Self::RegularPreviousCloseBelowMinimum => "regular_previous_close_below_minimum",
            Self::OfficialLuldUnavailable => "official_luld_unavailable",
            Self::InsideLuldBuffer => "inside_luld_buffer",
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Admission {
    pub block: Option<Block>,
    pub bands: Option<Buffered>,
}
fn blocked(reason: Block) -> Admission {
    Admission {
        block: Some(reason),
        bands: None,
    }
}
/// All prices share the instrument scale. The quote authority must separately
/// certify quote age and scope. This function never grants broker permission.
#[allow(clippy::too_many_arguments)]
pub fn regular_admission(
    scope: Scope,
    evaluated_at_ns: u64,
    previous_close: Option<i64>,
    bid: i64,
    ask: i64,
    evidence: Option<&Evidence>,
    policy: &Policy,
) -> Result<Admission> {
    if policy.tick <= 0
        || policy.scale > 9
        || policy.buffer_ticks < 3
        || policy.maximum_age_ns == 0
        || policy.minimum_previous_close <= 0
        || policy.buffer_bps.atoms < 0
        || policy.buffer_bps.scale > 9
        || bid <= 0
        || ask < bid
        || scope.provider == 0
        || scope.instrument == 0
    {
        return Err(Error::Invalid("regular LULD configuration or quote".into()));
    }
    let Some(previous_close) = previous_close.filter(|p| *p > 0) else {
        return Ok(blocked(Block::RegularPreviousCloseUnavailable));
    };
    if previous_close < policy.minimum_previous_close {
        return Ok(blocked(Block::RegularPreviousCloseBelowMinimum));
    }
    let Some(band) = evidence.filter(|band| {
        band.require(scope, policy.scale, evaluated_at_ns, policy.maximum_age_ns)
            .is_ok()
    }) else {
        return Ok(blocked(Block::OfficialLuldUnavailable));
    };
    let tick = i128::from(policy.tick);
    let fixed = tick * i128::from(policy.buffer_ticks);
    let spread = if policy.include_spread {
        i128::from(ask - bid)
    } else {
        0
    };
    let divisor = 10_000_i128 * 10_i128.pow(u32::from(policy.buffer_bps.scale));
    let buffer = |price: i64| {
        let numerator = i128::from(price) * i128::from(policy.buffer_bps.atoms);
        ((numerator + divisor - 1) / divisor).max(fixed).max(spread)
    };
    let lower = i128::from(band.lower) + buffer(band.lower);
    let upper = i128::from(band.upper) - buffer(band.upper);
    // Exact tick rounding inward. Never use float epsilon at a risk boundary.
    let lower = ((lower + tick - 1) / tick) * tick;
    let upper = upper.div_euclid(tick) * tick;
    if lower >= upper || upper <= 0 || lower > i128::from(i64::MAX) {
        return Ok(blocked(Block::InsideLuldBuffer));
    }
    let buffered = Buffered {
        lower_exit: lower as i64,
        target: upper as i64,
        scale: band.scale,
        effective_at_ns: band.effective_at_ns,
        available_at_ns: band.available_at_ns,
    };
    let block =
        (bid <= buffered.lower_exit || ask >= buffered.target).then_some(Block::InsideLuldBuffer);
    Ok(Admission {
        block,
        bands: Some(buffered),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Scope, Evidence, Policy) {
        (
            Scope {
                provider: 1,
                instrument: 1,
                session: 20260915,
            },
            Evidence {
                provider: 1,
                instrument: 1,
                session: 20260915,
                lower: 900,
                upper: 1100,
                scale: 2,
                effective_at_ns: 10,
                available_at_ns: 11,
                official: true,
            },
            Policy {
                tick: 1,
                scale: 2,
                buffer_ticks: 3,
                buffer_bps: Decimal { atoms: 5, scale: 0 },
                include_spread: true,
                maximum_age_ns: 10,
                minimum_previous_close: 500,
            },
        )
    }
    #[test]
    fn exact_buffers_and_strict_quote_boundaries() {
        let (scope, band, mut policy) = fixture();
        let result =
            regular_admission(scope, 15, Some(1000), 990, 1000, Some(&band), &policy).unwrap();
        assert!(result.block.is_none());
        let bands = result.bands.unwrap();
        assert_eq!((bands.lower_exit, bands.target), (910, 1090));
        assert_eq!((bands.effective_at_ns, bands.available_at_ns), (10, 11));
        policy.include_spread = false;
        policy.tick = 5;
        policy.buffer_bps = Decimal {
            atoms: 1555,
            scale: 1,
        };
        let b = regular_admission(scope, 20, Some(1000), 990, 1000, Some(&band), &policy)
            .unwrap()
            .bands
            .unwrap();
        assert_eq!((b.lower_exit, b.target), (915, 1080));
        for (bid, ask) in [(915, 920), (1070, 1080)] {
            assert_eq!(
                regular_admission(scope, 20, Some(1000), bid, ask, Some(&band), &policy)
                    .unwrap()
                    .block,
                Some(Block::InsideLuldBuffer)
            );
        }
    }
    #[test]
    fn original_band_clock_and_scope_are_required() {
        let (scope, original, policy) = fixture();
        for case in 0..7 {
            let mut band = original.clone();
            match case {
                0 => band.provider = 2,
                1 => band.instrument = 2,
                2 => band.session -= 1,
                3 => band.official = false,
                4 => band.scale = 3,
                5 => band.available_at_ns = 22,
                _ => band.effective_at_ns = 12,
            }
            assert_eq!(
                regular_admission(scope, 20, Some(1000), 990, 1000, Some(&band), &policy)
                    .unwrap()
                    .block,
                Some(Block::OfficialLuldUnavailable)
            );
        }
        let mut restamped = original;
        restamped.available_at_ns = 21;
        assert_eq!(
            regular_admission(scope, 21, Some(1000), 990, 1000, Some(&restamped), &policy)
                .unwrap()
                .block,
            Some(Block::OfficialLuldUnavailable)
        );
    }
    #[test]
    fn missing_dependencies_and_infeasible_buffer_never_admit() {
        let (scope, band, mut policy) = fixture();
        assert_eq!(
            regular_admission(scope, 15, None, 990, 1000, None, &policy)
                .unwrap()
                .block,
            Some(Block::RegularPreviousCloseUnavailable)
        );
        assert_eq!(
            regular_admission(scope, 15, Some(499), 990, 1000, None, &policy)
                .unwrap()
                .block,
            Some(Block::RegularPreviousCloseBelowMinimum)
        );
        assert_eq!(
            regular_admission(scope, 15, Some(1000), 990, 1000, None, &policy)
                .unwrap()
                .block,
            Some(Block::OfficialLuldUnavailable)
        );
        policy.buffer_ticks = u32::MAX;
        assert_eq!(
            regular_admission(scope, 15, Some(1000), 990, 1000, Some(&band), &policy)
                .unwrap()
                .block,
            Some(Block::InsideLuldBuffer)
        );
        policy.buffer_ticks = 2;
        assert!(regular_admission(scope, 15, Some(1000), 990, 1000, Some(&band), &policy).is_err());
    }
}

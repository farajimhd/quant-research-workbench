//! Student-t reaction bands and conservative two-component BIC partition.
use crate::v7_fit::{fit, Fit, Status};
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Observation {
    pub price: f64,
    pub resolution: f64,
    pub at: u64,
    pub resolved_at: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Band {
    pub fit: Fit,
    pub coverage: f64,
    pub resolution: Option<f64>,
    pub lower: Option<f64>,
    pub upper: Option<f64>,
    pub valid_positive_interval: bool,
}
impl Band {
    pub fn estimated(&self) -> bool {
        self.fit.status == Status::Estimated && self.valid_positive_interval
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Component {
    pub observations: Vec<Observation>,
    pub band: Band,
}
/// For df=4, CDF(x)=1/2+3z/4-z^3/4, z=x/sqrt(x*x+4).
/// Invert the bounded z coordinate rather than searching an unbounded tail.
fn quantile(coverage: f64) -> f64 {
    let target = (1. + coverage) / 2.;
    let (mut low, mut high) = (0., 1.);
    for _ in 0..64 {
        let mid = (low + high) / 2.;
        if 0.5 + 0.75 * mid - 0.25 * mid * mid * mid < target {
            low = mid;
        } else {
            high = mid;
        }
    }
    let z = (low + high) / 2.;
    2. * z / (1. - z * z).sqrt()
}
fn log_pdf(price: f64, band: &Band) -> f64 {
    let scale = band.fit.scale.unwrap();
    let z = (price - band.fit.center.unwrap()) / scale;
    (3_f64 / 8.).ln() - scale.ln() - 2.5 * (z * z / 4.).ln_1p()
}
pub fn estimate(observations: &[Observation], coverage: f64) -> Result<Band> {
    if !coverage.is_finite() || coverage <= 0. || coverage >= 1. {
        return Err(Error::Invalid(
            "band coverage must lie between zero and one".into(),
        ));
    }
    if observations
        .iter()
        .any(|o| !o.resolution.is_finite() || o.resolution <= 0. || o.resolved_at < o.at)
    {
        return Err(Error::Invalid(
            "invalid reaction observation resolution or interval".into(),
        ));
    }
    let resolution = observations.iter().map(|o| o.resolution).reduce(f64::min);
    let prices: Vec<_> = observations.iter().map(|o| o.price).collect();
    let fitted = fit(&prices, resolution.unwrap_or(1.))?;
    let mut band = Band {
        fit: fitted,
        coverage,
        resolution,
        lower: None,
        upper: None,
        valid_positive_interval: false,
    };
    if band.fit.status == Status::Estimated {
        let half = quantile(coverage) * band.fit.scale.unwrap();
        let center = band.fit.center.unwrap();
        if !half.is_finite() {
            return Err(Error::Invalid("band quantile overflow".into()));
        }
        band.lower = Some(center - half);
        band.upper = Some(center + half);
        band.valid_positive_interval = center - half > 0. && (center + half).is_finite();
    }
    Ok(band)
}
pub fn partition(observations: &[Observation], coverage: f64) -> Result<Vec<Component>> {
    let mut ordered = observations.to_vec();
    // Validate without fitting twice on the hot refit path.
    if ordered
        .iter()
        .any(|o| !o.price.is_finite() || o.price <= 0.)
    {
        return Err(Error::Invalid("invalid reaction price".into()));
    }
    ordered.sort_by(|a, b| {
        a.price
            .total_cmp(&b.price)
            .then(a.resolved_at.cmp(&b.resolved_at))
            .then(a.at.cmp(&b.at))
    });
    // Summation order is part of the source fitter, so refit in canonical order.
    let one = estimate(&ordered, coverage)?;
    let n = ordered.len();
    if n < 6 || !one.estimated() {
        return Ok(vec![Component {
            observations: ordered,
            band: one,
        }]);
    }
    let baseline =
        -2. * ordered.iter().map(|o| log_pdf(o.price, &one)).sum::<f64>() + 2. * (n as f64).ln();
    if !baseline.is_finite() {
        return Err(Error::Invalid("single-band likelihood overflow".into()));
    }
    let mut choices: Vec<_> = (3..n - 2).collect();
    choices.sort_by(|&a, &b| {
        (ordered[b].price - ordered[b - 1].price)
            .total_cmp(&(ordered[a].price - ordered[a - 1].price))
            .then(a.cmp(&b))
    });
    choices.truncate(3);
    let mut best: Option<(f64, usize, Band, Band)> = None;
    for index in choices {
        if ordered[index].price - ordered[index - 1].price <= 2. * one.resolution.unwrap() {
            continue;
        }
        let left = estimate(&ordered[..index], coverage)?;
        let right = estimate(&ordered[index..], coverage)?;
        if !left.estimated() || !right.estimated() || left.upper.unwrap() >= right.lower.unwrap() {
            continue;
        }
        let weight = index as f64 / n as f64;
        let mut likelihood = 0.;
        for observation in &ordered {
            let a = weight.ln() + log_pdf(observation.price, &left);
            let b = (1. - weight).ln() + log_pdf(observation.price, &right);
            let maximum = a.max(b);
            likelihood += maximum + ((a - maximum).exp() + (b - maximum).exp()).ln();
        }
        let bic = -2. * likelihood + 5. * (n as f64).ln();
        if !bic.is_finite() {
            return Err(Error::Invalid("mixture likelihood overflow".into()));
        }
        if baseline - bic > 10. && best.as_ref().is_none_or(|previous| bic < previous.0) {
            best = Some((bic, index, left, right));
        }
    }
    if let Some((_, index, left, right)) = best {
        let right_observations = ordered.split_off(index);
        Ok(vec![
            Component {
                observations: ordered,
                band: left,
            },
            Component {
                observations: right_observations,
                band: right,
            },
        ])
    } else {
        Ok(vec![Component {
            observations: ordered,
            band: one,
        }])
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn known_df4_quantile() {
        assert!((quantile(0.8) - 1.5332062740589432).abs() < 1e-12);
    }
    #[test]
    fn separated_components_are_fitted_independently() {
        let observations: Vec<_> = [10., 10.01, 9.99, 10.02, 20., 20.01, 19.99, 20.02]
            .iter()
            .enumerate()
            .map(|(i, &price)| Observation {
                price,
                resolution: 0.01,
                at: i as u64,
                resolved_at: i as u64,
            })
            .collect();
        let components = partition(&observations, 0.8).unwrap();
        assert_eq!(components.len(), 2);
        assert!(components[0].band.upper.unwrap() < components[1].band.lower.unwrap());
        assert_eq!(
            components
                .iter()
                .map(|c| c.observations.len())
                .sum::<usize>(),
            observations.len()
        );
    }
    #[test]
    fn insufficient_does_not_get_fallback_geometry() {
        let band = estimate(
            &[Observation {
                price: 10.,
                resolution: 0.01,
                at: 1,
                resolved_at: 2,
            }],
            0.8,
        )
        .unwrap();
        assert!(!band.estimated());
        assert_eq!(band.lower, None);
    }
}

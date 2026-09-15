//! Fixed-df Student-t MLE. The statistical objective, bounds and starts match
//! the frozen reference. The pure-Rust projected BFGS solver is separately
//! versioned; it is not represented as SciPy L-BFGS-B implementation parity.
use crate::v7_math::objective_gradient;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
pub const SOLVER_VERSION: &str = "arte-projected-bfgs-2d-1";
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    InsufficientEvidence,
    FitFailed,
    Estimated,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fit {
    pub count: usize,
    pub center: Option<f64>,
    pub scale: Option<f64>,
    pub status: Status,
    pub scale_at_floor: Option<bool>,
    pub projected_gradient: Option<f64>,
    pub solver_version: String,
}
fn quantile(sorted: &[f64], fraction: f64) -> f64 {
    let at = (sorted.len() - 1) as f64 * fraction;
    let lo = at.floor() as usize;
    let hi = at.ceil() as usize;
    sorted[lo] + (sorted[hi] - sorted[lo]) * (at - lo as f64)
}
fn dot(a: [f64; 2], b: [f64; 2]) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}
fn project(p: [f64; 2], bounds: [[f64; 2]; 2]) -> [f64; 2] {
    [
        p[0].clamp(bounds[0][0], bounds[0][1]),
        p[1].clamp(bounds[1][0], bounds[1][1]),
    ]
}
fn stationarity(p: [f64; 2], g: [f64; 2], bounds: [[f64; 2]; 2]) -> f64 {
    let q = project([p[0] - g[0], p[1] - g[1]], bounds);
    (p[0] - q[0]).abs().max((p[1] - q[1]).abs())
}
#[derive(Clone, Copy)]
struct Solution {
    p: [f64; 2],
    value: f64,
    residual: f64,
}
fn solve(start: [f64; 2], prices: &[f64], bounds: [[f64; 2]; 2]) -> Result<Option<Solution>> {
    let mut p = project(start, bounds);
    let (mut value, mut gradient) = objective_gradient(p[0], p[1], prices)?;
    let mut inverse = [[1., 0.], [0., 1.]];
    for _ in 0..1000 {
        let residual = stationarity(p, gradient, bounds);
        if residual <= 1e-8 {
            return Ok(Some(Solution { p, value, residual }));
        }
        let mut direction = [-dot(inverse[0], gradient), -dot(inverse[1], gradient)];
        for i in 0..2 {
            if (p[i] <= bounds[i][0] && direction[i] < 0.)
                || (p[i] >= bounds[i][1] && direction[i] > 0.)
            {
                direction[i] = 0.;
            }
        }
        if !dot(direction, gradient).is_finite() || dot(direction, gradient) >= -1e-20 {
            direction = [-gradient[0], -gradient[1]];
            inverse = [[1., 0.], [0., 1.]];
        }
        let mut accepted = None;
        let mut step = 1.;
        for _ in 0..50 {
            let next = project(
                [p[0] + step * direction[0], p[1] + step * direction[1]],
                bounds,
            );
            let displacement = [next[0] - p[0], next[1] - p[1]];
            if displacement == [0., 0.] {
                break;
            }
            let (new_value, new_gradient) = objective_gradient(next[0], next[1], prices)?;
            if new_value <= value + 1e-4 * dot(gradient, displacement) {
                accepted = Some((next, new_value, new_gradient, displacement));
                break;
            }
            step *= 0.5;
        }
        let Some((next, new_value, new_gradient, s)) = accepted else {
            return Ok((residual <= 1e-6).then_some(Solution { p, value, residual }));
        };
        let y = [new_gradient[0] - gradient[0], new_gradient[1] - gradient[1]];
        let sy = dot(s, y);
        if sy > 1e-14 * dot(s, s).sqrt() * dot(y, y).sqrt() && sy > 0. {
            let hy = [dot(inverse[0], y), dot(inverse[1], y)];
            let factor = (sy + dot(y, hy)) / (sy * sy);
            for i in 0..2 {
                for j in 0..2 {
                    inverse[i][j] += factor * s[i] * s[j] - (hy[i] * s[j] + s[i] * hy[j]) / sy;
                }
            }
            if inverse.iter().flatten().any(|v| !v.is_finite()) {
                inverse = [[1., 0.], [0., 1.]];
            }
        } else {
            inverse = [[1., 0.], [0., 1.]];
        }
        p = next;
        value = new_value;
        gradient = new_gradient;
    }
    let residual = stationarity(p, gradient, bounds);
    Ok((residual <= 1e-6).then_some(Solution { p, value, residual }))
}
pub fn fit(prices: &[f64], tick: f64) -> Result<Fit> {
    if !tick.is_finite() || tick <= 0. || prices.iter().any(|p| !p.is_finite() || *p <= 0.) {
        return Err(Error::Invalid(
            "invalid reaction prices or resolution".into(),
        ));
    }
    let mut result = Fit {
        count: prices.len(),
        center: None,
        scale: None,
        status: Status::InsufficientEvidence,
        scale_at_floor: None,
        projected_gradient: None,
        solver_version: SOLVER_VERSION.into(),
    };
    if prices.len() < 3 {
        return Ok(result);
    }
    let mut sorted = prices.to_vec();
    sorted.sort_by(f64::total_cmp);
    let origin = quantile(&sorted, 0.5);
    let y: Vec<_> = prices.iter().map(|x| (x - origin) / tick).collect();
    if y.iter().any(|v| !v.is_finite()) {
        return Err(Error::Invalid("reaction coordinate overflow".into()));
    }
    let mut ordered = y.clone();
    ordered.sort_by(f64::total_cmp);
    let mut absolute: Vec<_> = y.iter().map(|x| x.abs()).collect();
    absolute.sort_by(f64::total_cmp);
    let spread = (quantile(&absolute, 0.5) * 1.4826).max(0.5);
    let maximum_scale = ((ordered.last().unwrap() - ordered[0]) * 2.).max(1.);
    if !maximum_scale.is_finite() || !spread.is_finite() {
        return Err(Error::Invalid("reaction spread overflow".into()));
    }
    let bounds = [
        [ordered[0], *ordered.last().unwrap()],
        [0.5_f64.ln(), maximum_scale.ln()],
    ];
    let mut starts: Vec<_> = [0.25, 0.5, 0.75]
        .iter()
        .map(|&q| quantile(&ordered, q))
        .collect();
    starts.dedup();
    let mut best: Option<Solution> = None;
    for location in starts {
        if let Some(solution) = solve([location, spread.ln()], &y, bounds)? {
            if best.is_none_or(|previous| {
                solution.value < previous.value
                    || (solution.value == previous.value && solution.p[0] < previous.p[0])
            }) {
                best = Some(solution);
            }
        }
    }
    let Some(best) = best else {
        result.status = Status::FitFailed;
        return Ok(result);
    };
    let center = origin + tick * best.p[0];
    let scale = tick * best.p[1].exp();
    if !center.is_finite() || !scale.is_finite() || center <= 0. || scale <= 0. {
        return Err(Error::Invalid("reaction fit overflow".into()));
    }
    result.center = Some(center);
    result.scale = Some(scale);
    result.status = Status::Estimated;
    result.scale_at_floor = Some(best.p[1].exp() <= 0.5 * 1.00001);
    result.projected_gradient = Some(best.residual);
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn constant_prices_have_exact_floor() {
        let value = fit(&[10., 10., 10.], 0.01).unwrap();
        assert_eq!(value.status, Status::Estimated);
        assert_eq!(value.center, Some(10.));
        assert_eq!(value.scale, Some(0.005));
        assert_eq!(value.scale_at_floor, Some(true));
    }
    #[test]
    fn symmetric_prices_and_scaled_coordinates() {
        let prices = [9.8, 9.9, 10., 10.1, 10.2];
        let value = fit(&prices, 0.01).unwrap();
        assert_eq!(value.status, Status::Estimated);
        assert!((value.center.unwrap() - 10.).abs() < 1e-7);
        assert!(value.projected_gradient.unwrap() <= 1e-6);
        let other = fit(&prices.map(|p| p * 2.), 0.02).unwrap();
        assert!((other.scale.unwrap() - 2. * value.scale.unwrap()).abs() < 1e-8);
    }
    #[test]
    fn insufficient_and_invalid_remain_distinct() {
        assert_eq!(
            fit(&[10., 11.], 0.01).unwrap().status,
            Status::InsufficientEvidence
        );
        assert!(fit(&[10., f64::NAN, 11.], 0.01).is_err());
    }
}

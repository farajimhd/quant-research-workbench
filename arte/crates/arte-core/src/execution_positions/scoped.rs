//! Strategy attribution over the same FIFO algorithm and unchanged fill contract.
//! The caller must resolve command ownership from its execution authority.
use super::*;
use crate::{seed_storage::Object, strategy_dispatch::Scope};

pub struct Scoped {
    scope: Scope,
    key: Key,
    projection: Projection,
}
impl Scoped {
    pub fn new(
        scope: Scope,
        origin_hash: String,
        maximum_fills: usize,
        maximum_lots: usize,
    ) -> Result<Self> {
        crate::strategy_dispatch::State::new(scope.clone())?;
        if origin_hash.len() != 64
            || !origin_hash
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err(Error::Invalid("strategy projection origin hash".into()));
        }
        let key = Key {
            origin_hash,
            account: scope.account.clone(),
            instrument: scope.instrument,
        };
        Ok(Self {
            scope,
            key,
            projection: Projection::new(1, maximum_fills, maximum_lots)?,
        })
    }
    pub fn scope(&self) -> &Scope {
        &self.scope
    }
    pub fn position(&self) -> Option<&Position> {
        self.projection.position(&self.key)
    }
    /// Only after durable fill acknowledgment. Ownership is supplied separately;
    /// never rewrite a fill's account or origin to produce a strategy partition.
    pub fn apply(&mut self, owner: &Scope, fill: &Fill) -> Result<bool> {
        if owner != &self.scope || Key::from_fill(fill)? != self.key {
            return Err(Error::Conflict(
                "strategy fill ownership or origin differs".into(),
            ));
        }
        if let Origin::Simulated { run_id, .. } = &fill.origin {
            if owner.mode != crate::strategy_dispatch::Mode::Backtest || &owner.run_id != run_id {
                return Err(Error::Conflict("strategy simulation run differs".into()));
            }
        } else if let Origin::Broker { paper, .. } = &fill.origin {
            let expected = if *paper {
                crate::strategy_dispatch::Mode::Paper
            } else {
                crate::strategy_dispatch::Mode::Live
            };
            if owner.mode != expected {
                return Err(Error::Conflict("broker fill trading mode differs".into()));
            }
        }
        self.projection.apply(fill)
    }
    fn context(&self, parent: &str) -> Result<String> {
        if parent.len() != 64
            || !parent
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err(Error::Invalid("strategy projection parent context".into()));
        }
        content_hash(&(
            "arte.strategy-projection.v1",
            parent,
            &self.scope,
            &self.key,
        ))
    }
    pub fn checkpoint(&self, parent: &str, limits: checkpoint::Limits) -> Result<Object> {
        self.projection.checkpoint(&self.context(parent)?, limits)
    }
    pub fn restore(
        scope: Scope,
        origin_hash: String,
        object: &Object,
        expected_hash: &str,
        parent: &str,
        limits: checkpoint::Limits,
    ) -> Result<Self> {
        let mut restored = Self::new(scope, origin_hash, limits.fills, limits.lots_per_position)?;
        restored.projection = Projection::restore_checkpoint(
            object,
            expected_hash,
            &restored.context(parent)?,
            limits,
        )?;
        if restored
            .projection
            .positions()
            .any(|(key, _)| key != &restored.key)
        {
            return Err(Error::Conflict(
                "strategy projection checkpoint key differs".into(),
            ));
        }
        Ok(restored)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn scope(strategy: &str) -> Scope {
        Scope {
            run_id: "run".into(),
            mode: crate::strategy_dispatch::Mode::Backtest,
            account: "a".into(),
            instrument: 1,
            strategy_instance: strategy.into(),
            strategy_kind: crate::strategy_dispatch::StrategyKind::GenericCandidate,
            config_hash: "a".repeat(64),
            code_hash: "c".repeat(64),
        }
    }
    #[test]
    fn same_account_strategies_keep_separate_fifo_and_recovery_identity() {
        let mut fill = Fill {
            schema_version: 1,
            origin: Origin::Simulated {
                run_id: "run".into(),
                model: "model".into(),
            },
            command_id: "one".into(),
            account: "a".into(),
            instrument: 1,
            price_scale: 2,
            sequence: 1,
            at_ns: 1,
            executed_at_ns: Some(1),
            leg: Leg::Entry,
            direction: Direction::Buy,
            quantity: 2,
            price: 100,
        };
        let origin = Key::from_fill(&fill).unwrap().origin_hash;
        let mut one = Scoped::new(scope("one"), origin.clone(), 10, 4).unwrap();
        let mut two = Scoped::new(scope("two"), origin.clone(), 10, 4).unwrap();
        one.apply(&scope("one"), &fill).unwrap();
        assert!(!one.apply(&scope("one"), &fill).unwrap());
        assert!(two.apply(&scope("one"), &fill).is_err());
        fill.command_id = "two".into();
        fill.price = 200;
        two.apply(&scope("two"), &fill).unwrap();
        assert_eq!(one.position().unwrap().open_cost_atoms, 200);
        assert_eq!(two.position().unwrap().open_cost_atoms, 400);
        let limits = checkpoint::Limits {
            positions: 1,
            fills: 10,
            lots_per_position: 4,
            bytes: 10000,
        };
        let parent = "b".repeat(64);
        let image = one.checkpoint(&parent, limits).unwrap();
        assert!(Scoped::restore(
            scope("two"),
            origin.clone(),
            &image,
            &image.id,
            &parent,
            limits
        )
        .is_err());
        let mut restored =
            Scoped::restore(scope("one"), origin, &image, &image.id, &parent, limits).unwrap();
        fill.command_id = "one".into();
        fill.sequence = 2;
        fill.at_ns = 2;
        fill.executed_at_ns = Some(2);
        fill.leg = Leg::Exit;
        fill.direction = Direction::Sell;
        fill.quantity = 1;
        fill.price = 150;
        restored.apply(&scope("one"), &fill).unwrap();
        assert_eq!(restored.position().unwrap().quantity, 1);
        assert_eq!(restored.position().unwrap().open_cost_atoms, 100);
        assert_eq!(restored.position().unwrap().realized_gross_pnl_atoms, 50);
        assert_eq!(two.position().unwrap().quantity, 2);
    }
}

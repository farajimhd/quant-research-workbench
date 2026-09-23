//! Bounded ingestion actor. No connection or task starts at construction.
use crate::live_decode::{AuditedEvent, Decoder, SilenceAssessment};
use crate::massive_stream::{Health, ReceivedFrame};
use arte_core::exposure::{Check, Gate};
use arte_core::{Error, Result};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, RwLock,
};
use std::time::Duration;
use tokio::sync::{mpsc, watch};

/// Readiness is borrowed under a short read lock, never copied into an order queue.
/// Callbacks must not perform I/O or wait. Contention fails closed instead of waiting.
#[derive(Clone)]
pub struct MarketReadiness(Arc<Shared>);
struct Shared {
    running: AtomicBool,
    gate: RwLock<Gate>,
}
impl MarketReadiness {
    pub fn check<T>(
        &self,
        now_monotonic_ns: u64,
        action: impl FnOnce(Check<'_>) -> Result<T>,
    ) -> Result<T> {
        if !self.0.running.load(Ordering::Acquire) {
            return Err(Error::Unready("ingestion actor stopped".into()));
        }
        let gate = self
            .0
            .gate
            .try_read()
            .map_err(|_| Error::Unready("market readiness busy or poisoned".into()))?;
        if !self.0.running.load(Ordering::Acquire) {
            return Err(Error::Unready("ingestion actor stopped".into()));
        }
        action(gate.at(now_monotonic_ns))
    }
}
struct Running(MarketReadiness);
impl Drop for Running {
    fn drop(&mut self) {
        self.0 .0.running.store(false, Ordering::Release);
    }
}

#[derive(Debug)]
pub enum Batch {
    Events(Vec<AuditedEvent>),
    Silence(Vec<SilenceAssessment>),
}
#[derive(Debug)]
pub struct Failure {
    pub error: Error,
    /// Preserve invalid or not-yet-delivered input for explicit repair/audit.
    pub frame: Option<ReceivedFrame>,
    pub undelivered: Option<Batch>,
}
impl Failure {
    fn new(error: Error) -> Box<Self> {
        Box::new(Self {
            error,
            frame: None,
            undelivered: None,
        })
    }
}
#[derive(Clone, Copy)]
pub struct ClockReading {
    /// Same run-relative monotonic origin as the receiver.
    pub monotonic_ns: u64,
    /// Caller must return an error if clock-quality evidence is unavailable/stale.
    pub uncertainty_ns: u64,
}
pub struct Channels<'a> {
    /// Borrowed: queued frames remain owned by the supervisor on failure or shutdown.
    pub frames: &'a mut mpsc::Receiver<ReceivedFrame>,
    pub transport: watch::Receiver<Health>,
    pub shutdown: watch::Receiver<bool>,
    /// Domain ingestion output, not a chart/observer queue.
    pub output: mpsc::Sender<Batch>,
}
pub struct Pipeline {
    decoder: Decoder,
    readiness: MarketReadiness,
    audit_every: Duration,
    started: bool,
}
impl Pipeline {
    pub fn new(decoder: Decoder, mut gate: Gate, audit_every: Duration) -> Result<Self> {
        if audit_every.is_zero() || audit_every > Duration::from_secs(60) {
            return Err(Error::Invalid("invalid silence audit interval".into()));
        }
        gate.transport(false);
        Ok(Self {
            decoder,
            readiness: MarketReadiness(Arc::new(Shared {
                running: AtomicBool::new(false),
                gate: RwLock::new(gate),
            })),
            audit_every,
            started: false,
        })
    }
    pub fn readiness(&self) -> MarketReadiness {
        self.readiness.clone()
    }

    /// Starts no receiver. Supervisor must retain/join that task and repair failures.
    /// One shot: reconnect requires a new decoder and certified recovery boundary.
    pub async fn run(
        &mut self,
        mut channels: Channels<'_>,
        mut clock: impl FnMut() -> Result<ClockReading>,
        mut resolve: impl FnMut(&str, u64, u64) -> Result<u64>,
    ) -> std::result::Result<(), Box<Failure>> {
        if self.started {
            return Err(Failure::new(Error::Unready(
                "ingestion pipeline cannot restart".into(),
            )));
        }
        self.started = true;
        self.readiness.0.running.store(true, Ordering::Release);
        let _running = Running(self.readiness.clone());
        let mut audit = tokio::time::interval(self.audit_every);
        audit.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            if *channels.shutdown.borrow() {
                return Ok(());
            }
            let health = channels.transport.borrow_and_update().clone();
            self.transport(&health).map_err(Failure::new)?;
            tokio::select! {
                changed = channels.shutdown.changed() => {
                    if changed.is_err() || *channels.shutdown.borrow() { return Ok(()); }
                }
                changed = channels.transport.changed() => {
                    if changed.is_err() { return Err(Failure::new(Error::Unready("transport health channel closed".into()))); }
                }
                _ = audit.tick() => {
                    let reading = clock().map_err(Failure::new)?;
                    let assessments = {
                        let mut gate = self.readiness.0.gate.try_write().map_err(|_| Failure::new(Error::Unready("market readiness writer contention".into())))?;
                        self.decoder.audit_to_gate(reading.monotonic_ns, reading.uncertainty_ns, &mut gate).map_err(Failure::new)?
                    };
                    if !assessments.is_empty() { deliver(&channels.output, Batch::Silence(assessments), None)?; }
                }
                frame = channels.frames.recv() => {
                    let frame = frame.ok_or_else(|| Failure::new(Error::Unready("market input ended; repair required".into())))?;
                    let events = (|| {
                        // Recheck after dequeue, not only before select.
                        self.transport(&channels.transport.borrow())?;
                        let reading = clock()?;
                        let mut gate = self.readiness.0.gate.try_write().map_err(|_| Error::Unready("market readiness writer contention".into()))?;
                        self.decoder.decode_to_gate(&frame, reading.monotonic_ns, reading.uncertainty_ns, &mut gate, &mut resolve)
                    })();
                    match events {
                        Ok(events) => deliver(&channels.output, Batch::Events(events), Some(frame))?,
                        Err(error) => return Err(Box::new(Failure { error, frame: Some(frame), undelivered: None })),
                    }
                }
            }
        }
    }
    fn transport(&self, health: &Health) -> Result<()> {
        if matches!(health, Health::Failed | Health::Stopped) {
            return Err(Error::Unready(
                "market transport ended; repair required".into(),
            ));
        }
        let mut gate = self
            .readiness
            .0
            .gate
            .try_write()
            .map_err(|_| Error::Unready("market readiness writer contention".into()))?;
        // A subscription request and observed traffic are diagnostics. Massive
        // does not supply an authenticated per-channel completeness receipt
        // here, so neither state may arm exposure.
        gate.transport(false);
        Ok(())
    }
}
fn deliver(
    output: &mpsc::Sender<Batch>,
    batch: Batch,
    frame: Option<ReceivedFrame>,
) -> std::result::Result<(), Box<Failure>> {
    output.try_send(batch).map_err(|error| {
        Box::new(Failure {
            error: Error::Capacity("domain ingestion queue unavailable; repair required".into()),
            frame,
            undelivered: Some(error.into_inner()),
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::latency::LatencyPolicy;
    fn pipeline() -> Pipeline {
        Pipeline::new(
            Decoder::new(
                "run".into(),
                1,
                16,
                8,
                LatencyPolicy {
                    warn_ns: 2_000_000,
                    block_ns: 5_000_000,
                    max_clock_uncertainty_ns: 1_000_000,
                    recovery_samples: 1,
                    repeat_ns: 1_000_000,
                },
                false,
            )
            .unwrap(),
            Gate::new(20_000_000, 8).unwrap(),
            Duration::from_millis(1),
        )
        .unwrap()
    }
    fn frame() -> ReceivedFrame {
        ReceivedFrame {text: r#"[{"ev":"T","sym":"AAPL","t":1000,"q":1,"p":10,"s":1,"x":1,"i":"trade"},{"ev":"Q","sym":"AAPL","t":1000,"q":2,"bp":9.99,"ap":10.01,"bs":10,"as":10,"bx":1,"ax":1}]"#.into(), utc_ns:1_000_000_100, monotonic_ns:100, frame_sequence:1}
    }
    #[tokio::test]
    async fn overflow_preserves_frame_and_batch_and_disarms() {
        let mut p = pipeline();
        let ready = p.readiness();
        let (input, mut frames) = mpsc::channel(2);
        input.try_send(frame()).unwrap();
        let (_transport, health) = watch::channel(Health::Receiving);
        let (_stop, shutdown) = watch::channel(false);
        let (output, _read) = mpsc::channel(1);
        output.try_send(Batch::Silence(vec![])).unwrap();
        let failure = p
            .run(
                Channels {
                    frames: &mut frames,
                    transport: health,
                    shutdown,
                    output,
                },
                || {
                    Ok(ClockReading {
                        monotonic_ns: 100,
                        uncertainty_ns: 0,
                    })
                },
                |_, _, _| Ok(1),
            )
            .await
            .unwrap_err();
        assert!(failure.frame.is_some());
        assert!(matches!(failure.undelivered, Some(Batch::Events(events)) if events.len()==2));
        assert!(ready.check(100, |g| g.require(1)).is_err());
    }
    #[tokio::test]
    async fn stopped_transport_keeps_queued_input_for_supervisor() {
        let mut p = pipeline();
        let (input, mut frames) = mpsc::channel(1);
        input.try_send(frame()).unwrap();
        let (_transport, health) = watch::channel(Health::Failed);
        let (_stop, shutdown) = watch::channel(false);
        let (output, _read) = mpsc::channel(1);
        assert!(p
            .run(
                Channels {
                    frames: &mut frames,
                    transport: health,
                    shutdown,
                    output
                },
                || panic!("must not decode"),
                |_, _, _| panic!("must not resolve")
            )
            .await
            .is_err());
        assert_eq!(frames.len(), 1);
        assert!(p.readiness().check(100, |g| g.require(1)).is_err());
    }
    #[test]
    fn cancellation_guard_disarms_shared_handle() {
        let p = pipeline();
        p.readiness.0.running.store(true, Ordering::Release);
        {
            let _guard = Running(p.readiness());
        }
        assert!(p.readiness().check(100, |_| Ok(())).is_err());
    }
    #[test]
    fn subscribe_sent_and_received_traffic_do_not_certify_feed_coverage() {
        let p = pipeline();
        p.readiness.0.running.store(true, Ordering::Release);
        for health in [Health::SubscriptionRequested, Health::Receiving] {
            p.readiness.0.gate.write().unwrap().transport(true);
            p.transport(&health).unwrap();
            let mut gate = p.readiness.0.gate.write().unwrap();
            for kind in [
                arte_core::events::EventKind::Trade,
                arte_core::events::EventKind::Quote,
            ] {
                gate.update(1, kind, 100, true).unwrap();
            }
            assert!(gate.at(100).require(1).is_err());
        }
    }
    #[tokio::test]
    async fn periodic_audits_block_silence_and_repeat_without_incoming_frames() {
        use std::sync::atomic::AtomicU64;
        let mut p = pipeline();
        let ready = p.readiness();
        let now = AtomicU64::new(100);
        let (input, mut frames) = mpsc::channel(1);
        input.try_send(frame()).unwrap();
        let (_transport, health) = watch::channel(Health::Receiving);
        let (stop, shutdown) = watch::channel(false);
        let (output, mut read) = mpsc::channel(16);
        let run = p.run(
            Channels {
                frames: &mut frames,
                transport: health,
                shutdown,
                output,
            },
            || {
                Ok(ClockReading {
                    monotonic_ns: now.load(Ordering::Acquire),
                    uncertainty_ns: 0,
                })
            },
            |_, _, _| Ok(1),
        );
        let observe = async {
            assert!(matches!(read.recv().await, Some(Batch::Events(_))));
            ready
                .check(100, |g| {
                    assert!(g.require(1).is_err());
                    Ok(())
                })
                .unwrap();
            now.store(10_000_100, Ordering::Release);
            let mut alerts = 0;
            while alerts < 2 {
                if let Some(Batch::Silence(rows)) = read.recv().await {
                    if rows
                        .iter()
                        .all(|r| !r.exposure_permitted && r.sip_latency.notify)
                    {
                        ready
                            .check(now.load(Ordering::Acquire), |g| {
                                assert!(g.require(1).is_err());
                                Ok(())
                            })
                            .unwrap();
                        alerts += 1;
                        now.fetch_add(1_000_000, Ordering::AcqRel);
                    }
                }
            }
            stop.send_replace(true);
        };
        tokio::time::timeout(Duration::from_secs(2), async {
            let (result, ()) = tokio::join!(run, observe);
            result.unwrap();
        })
        .await
        .unwrap();
        assert!(ready.check(100, |_| Ok(())).is_err());
    }
}

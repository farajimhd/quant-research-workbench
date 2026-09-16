//! Read-only historical source assembly. No source selection, writes or services.
use super::{projection, Source};
use arte_core::{
    acquisition::{Certificate, Verifier},
    coverage::Interval,
    event_order::Scope,
    events::EventKind,
    market_structure::scheduler::playback,
    trade_eligibility::Pinned,
    Error, Result,
};
use std::future::Future;
pub trait Reader: super::Reader {
    fn certificate(&self, id: &str) -> impl Future<Output = Result<Certificate>> + Send;
    fn policy(
        &self,
        provider: u16,
        id: &str,
        as_of_ns: u64,
    ) -> impl Future<Output = Result<Pinned>> + Send;
}
impl Reader for crate::clickhouse::ClickHouse {
    async fn certificate(&self, id: &str) -> Result<Certificate> {
        self.load_acquisition_manifest(id).await
    }
    async fn policy(&self, provider: u16, id: &str, as_of_ns: u64) -> Result<Pinned> {
        self.load_trade_policy(provider, id, as_of_ns).await
    }
}
pub struct Request<'a> {
    pub scope: Scope,
    pub interval: Interval,
    pub trade_certificate: &'a str,
    pub quote_certificate: &'a str,
    pub trade_policy: &'a str,
    /// Source-generation knowledge cutoff, distinct from simulated session time.
    pub source_as_of_ns: u64,
    pub timing: projection::Policy,
}
pub struct Limits {
    /// Each retained channel has this budget. Both remain in the returned input.
    /// Channels load sequentially, so batch concurrency never doubles.
    pub channel: super::Limits,
    pub prepared: playback::Limits,
}
pub struct Input {
    pub trades: Source,
    pub quotes: Source,
    pub projection: projection::Projection,
}
fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn check(c: &Certificate, id: &str, kind: EventKind, request: &Request<'_>) -> Result<()> {
    if c.id()? != id
        || c.authority.kind != kind
        || c.authority.provider != request.scope.provider
        || c.authority.instrument != request.scope.instrument
        || c.interval != request.interval
        || c.published_at_ns > request.source_as_of_ns
    {
        return Err(Error::Conflict(
            "historical startup certificate identity, domain or cutoff".into(),
        ));
    }
    Verifier::new(c.clone())?;
    Ok(())
}
/// Load each event batch once. The metadata read cannot confer verified coverage;
/// only the shared source loader checks batch identities, payloads and counts.
/// Failures and cancellation return no partial Input and perform no writes.
pub async fn load(reader: &impl Reader, request: Request<'_>, limits: Limits) -> Result<Input> {
    load_inner(reader, request, limits, None).await
}
/// Historical market startup explicitly requests verified trade-second evidence.
/// Invalid domains never silently revert to unindexed loading.
pub async fn load_indexed(
    reader: &impl Reader,
    request: Request<'_>,
    limits: Limits,
    maximum_seconds: usize,
) -> Result<Input> {
    load_inner(reader, request, limits, Some(maximum_seconds)).await
}
async fn load_inner(
    reader: &impl Reader,
    request: Request<'_>,
    limits: Limits,
    index_seconds: Option<usize>,
) -> Result<Input> {
    request.interval.validate()?;
    if let Some(maximum) = index_seconds {
        if maximum == 0
            || maximum > 172_800
            || !request.interval.start.is_multiple_of(1_000_000_000)
            || !request.interval.end.is_multiple_of(1_000_000_000)
            || (request.interval.end - request.interval.start) / 1_000_000_000 > maximum as u64
        {
            return Err(Error::Invalid(
                "indexed startup interval or occupancy budget".into(),
            ));
        }
    }
    limits.channel.validate()?;
    if request.scope.provider == 0
        || request.scope.instrument == 0
        || !(19000101..=29991231).contains(&request.scope.session)
        || [
            request.trade_certificate,
            request.quote_certificate,
            request.trade_policy,
        ]
        .iter()
        .any(|id| !hash_valid(id))
        || request.trade_certificate == request.quote_certificate
        || request.timing.delay_ns == 0
        || request.timing.delay_ns > 1_000_000_000
        || limits.prepared.maximum_frames == 0
        || limits.prepared.maximum_frames > 10_000_000
        || limits.prepared.maximum_events == 0
        || limits.prepared.maximum_events > 10_000_000
        || limits.prepared.maximum_serialized_bytes == 0
    {
        return Err(Error::Invalid(
            "historical startup request or budget".into(),
        ));
    }
    let (trades, quotes) = tokio::try_join!(
        reader.certificate(request.trade_certificate),
        reader.certificate(request.quote_certificate)
    )?;
    check(
        &trades,
        request.trade_certificate,
        EventKind::Trade,
        &request,
    )?;
    check(
        &quotes,
        request.quote_certificate,
        EventKind::Quote,
        &request,
    )?;
    let rows = trades
        .pages
        .iter()
        .chain(&quotes.pages)
        .try_fold(0u64, |n, p| {
            n.checked_add(p.accepted_rows - p.deduplicated_rows)
        })
        .ok_or_else(|| Error::Capacity("historical startup row overflow".into()))?;
    if rows > limits.prepared.maximum_events as u64 {
        return Err(Error::Capacity(
            "combined replay source exceeds projection budget".into(),
        ));
    }
    let policy = reader
        .policy(
            request.scope.provider,
            request.trade_policy,
            request.interval.start,
        )
        .await?;
    if policy.hash() != request.trade_policy || policy.provider() != request.scope.provider {
        return Err(Error::Conflict(
            "historical startup trade policy differs".into(),
        ));
    }
    policy.require_interval(request.interval, request.interval.start)?;
    let trades = if let Some(maximum) = index_seconds {
        super::load_indexed_trades(
            reader,
            trades,
            request.trade_certificate,
            request.source_as_of_ns,
            limits.channel,
            request.scope,
            maximum,
        )
        .await?
    } else {
        super::load(
            reader,
            trades,
            request.trade_certificate,
            request.source_as_of_ns,
            limits.channel,
        )
        .await?
    };
    let quotes = super::load(
        reader,
        quotes,
        request.quote_certificate,
        request.source_as_of_ns,
        limits.channel,
    )
    .await?;
    let projection = projection::prepare_with_policy(
        &trades,
        &quotes,
        request.scope,
        request.timing,
        &policy,
        limits.prepared,
    )?;
    Ok(Input {
        trades,
        quotes,
        projection,
    })
}

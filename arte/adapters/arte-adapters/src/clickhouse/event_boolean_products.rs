//! Immutable event-cadence Boolean transitions and coverage publication.
//! The external source authority must separately certify the boundary ledger.
use super::*;
use arte_core::{
    config::Acceptance,
    coverage::Interval,
    event_boolean::{ledger::SourceProof, replay, Product, Transition},
    execution_interval::{ExecutionContract, ExecutionInterval},
    market_structure::scheduler::{playback::Playback, Boundary},
    quote_state::Book,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const TRANSITIONS: &str = "event_boolean_transitions_v1";
const PRODUCTS: &str = "event_boolean_products_v1";
const PAGE: usize = 1_000;

fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    product_hash: String,
    provider: u16,
    instrument: u64,
    session: u32,
    event_index: u64,
    boundary_id: String,
    source_sequence: u64,
    evaluated_at_ns: u64,
    known: u8,
    value: u8,
}
impl Row {
    fn from_transition(hash: &str, product: &Product, item: &Transition) -> Self {
        Self {
            product_hash: hash.into(),
            provider: product.provider,
            instrument: product.instrument,
            session: product.session,
            event_index: item.event_index,
            boundary_id: item.boundary_id.clone(),
            source_sequence: item.source_sequence,
            evaluated_at_ns: item.evaluated_at_ns,
            known: u8::from(item.value.is_some()),
            value: u8::from(item.value.unwrap_or(false)),
        }
    }
    fn transition(&self, hash: &str, header: &Header) -> Result<Transition> {
        if self.product_hash != hash
            || self.provider != header.provider
            || self.instrument != header.instrument
            || self.session != header.session
            || self.event_index >= header.event_count
            || self.known > 1
            || self.value > 1
            || (self.known == 0 && self.value != 0)
        {
            return Err(Error::Conflict("event Boolean row scope or state".into()));
        }
        Ok(Transition {
            event_index: self.event_index,
            boundary_id: self.boundary_id.clone(),
            source_sequence: self.source_sequence,
            evaluated_at_ns: self.evaluated_at_ns,
            value: (self.known == 1).then_some(self.value == 1),
        })
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Header {
    schema_version: u16,
    provider: u16,
    instrument: u64,
    session: u32,
    interval: Interval,
    definition_hash: String,
    source_authority_hash: String,
    source_certified_at_ns: u64,
    event_count: u64,
    source_hash: String,
    evaluation_hash: String,
    transition_count: usize,
    published_at_ns: u64,
}
impl Header {
    fn from_product(product: &Product, source: &ExpectedSource, published_at_ns: u64) -> Self {
        Self {
            schema_version: 1,
            provider: product.provider,
            instrument: product.instrument,
            session: product.session,
            interval: product.interval,
            definition_hash: product.definition_hash.clone(),
            source_authority_hash: source.authority_hash.clone(),
            source_certified_at_ns: source.certified_at_ns,
            event_count: product.event_count,
            source_hash: product.source_hash.clone(),
            evaluation_hash: product.evaluation_hash.clone(),
            transition_count: product.transitions.len(),
            published_at_ns,
        }
    }
    fn validate(&self, expected_authority: &str, as_of_ns: u64) -> Result<()> {
        self.interval.validate()?;
        if self.schema_version != 1
            || self.provider == 0
            || self.instrument == 0
            || !(19000101..=29991231).contains(&self.session)
            || !hash_valid(&self.definition_hash)
            || !hash_valid(&self.source_authority_hash)
            || self.source_authority_hash != expected_authority
            || !hash_valid(&self.source_hash)
            || !hash_valid(&self.evaluation_hash)
            || self.event_count > 100_000_000
            || self.transition_count > 10_000_000
            || self.transition_count as u64 > self.event_count
            || self.source_certified_at_ns < self.interval.end
            || self.source_certified_at_ns > self.published_at_ns
            || self.published_at_ns < self.interval.end
            || self.published_at_ns > as_of_ns
        {
            return Err(Error::Unready(
                "event Boolean header identity or availability".into(),
            ));
        }
        Ok(())
    }
    fn product(self, transitions: Vec<Transition>) -> Result<Product> {
        if transitions.len() != self.transition_count {
            return Err(Error::Unready(
                "event Boolean transition readback incomplete".into(),
            ));
        }
        let product = Product {
            provider: self.provider,
            instrument: self.instrument,
            session: self.session,
            interval: self.interval,
            definition_hash: self.definition_hash,
            event_count: self.event_count,
            source_hash: self.source_hash,
            evaluation_hash: self.evaluation_hash,
            transitions,
        };
        product.validate()?;
        Ok(product)
    }
}

pub struct Prepared {
    product: Product,
    header: Header,
    hash: String,
}

/// Boundary-ledger evidence from an independently verified source owner.
/// This value is only a contract; its origin still requires a trusted verifier.
struct ExpectedSource {
    authority_hash: String,
    event_count: u64,
    source_hash: String,
    certified_at_ns: u64,
}
impl Prepared {
    pub fn hash(&self) -> &str {
        &self.hash
    }
    pub fn product(&self) -> &Product {
        &self.product
    }
}

/// Pure preparation from a completed, catalogue-pinned shared playback proof.
pub fn prepare_event_boolean_product(
    product: Product,
    definition: &ExecutionContract,
    source: &SourceProof,
    published_at_ns: u64,
) -> Result<Prepared> {
    if product.provider != source.scope().provider
        || product.instrument != source.scope().instrument
        || product.session != source.scope().session
        || product.interval != source.interval()
        || product.definition_hash != source.definition_hash()
    {
        return Err(Error::Conflict(
            "event Boolean source proof domain differs".into(),
        ));
    }
    prepare_from_source(
        product,
        definition,
        &ExpectedSource {
            authority_hash: source.authority_hash().into(),
            event_count: source.event_count(),
            source_hash: source.source_hash().into(),
            certified_at_ns: source.certified_at_ns(),
        },
        published_at_ns,
    )
}

/// Pure offline composition of the shared event clock, independent source
/// proof, and immutable ClickHouse publication payload. Does not perform I/O.
pub fn prepare_replayed_event_boolean_product(
    playback: Playback,
    context: replay::Context<'_>,
    published_at_ns: u64,
    evaluate: impl FnMut(
        &Boundary<'_>,
        &arte_core::market_structure::Runtime,
        &Book,
    ) -> Result<Option<bool>>,
) -> Result<Prepared> {
    let definition = context.definition;
    let (product, proof) = replay::project(playback, context, evaluate)?;
    prepare_event_boolean_product(product, definition, &proof, published_at_ns)
}

fn prepare_from_source(
    product: Product,
    definition: &ExecutionContract,
    source: &ExpectedSource,
    published_at_ns: u64,
) -> Result<Prepared> {
    if definition.interval != ExecutionInterval::Events
        || definition.hash()? != product.definition_hash
        || !hash_valid(&source.authority_hash)
        || !hash_valid(&source.source_hash)
        || source.event_count != product.event_count
        || source.source_hash != product.source_hash
    {
        return Err(Error::Conflict(
            "event Boolean definition or source authority".into(),
        ));
    }
    let hash = product.hash()?;
    let header = Header::from_product(&product, source, published_at_ns);
    header.validate(&source.authority_hash, published_at_ns)?;
    Ok(Prepared {
        product,
        header,
        hash,
    })
}

pub fn event_boolean_publication_scope(prepared: &Prepared) -> Result<String> {
    arte_core::content_hash(&(
        "arte.event-boolean-publication.v1",
        prepared.product.provider,
        prepared.product.instrument,
        prepared.product.session,
        prepared.product.interval.start,
        prepared.product.interval.end,
        &prepared.product.definition_hash,
    ))
}

fn decode_rows(body: &str, hash: &str, header: &Header, after: Option<u64>) -> Result<Vec<Row>> {
    let mut rows = Vec::new();
    let mut last = after;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if rows.len() > PAGE {
            return Err(Error::Capacity("event Boolean read page".into()));
        }
        let row: Row = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("event Boolean row JSON".into()))?;
        row.transition(hash, header)?;
        if last.is_some_and(|index| row.event_index <= index) {
            return Err(Error::Conflict(
                "event Boolean row order or duplicate".into(),
            ));
        }
        last = Some(row.event_index);
        rows.push(row);
    }
    Ok(rows)
}

fn missing_rows(prepared: &Prepared, existing: &[Row]) -> Result<Vec<Row>> {
    let by_index: std::collections::BTreeMap<_, _> =
        existing.iter().map(|row| (row.event_index, row)).collect();
    if by_index.len() != existing.len() {
        return Err(Error::Conflict(
            "duplicate event Boolean persisted row".into(),
        ));
    }
    let mut missing = Vec::new();
    for item in &prepared.product.transitions {
        let expected = Row::from_transition(&prepared.hash, &prepared.product, item);
        match by_index.get(&item.event_index) {
            Some(previous) if **previous == expected => {}
            Some(_) => {
                return Err(Error::Conflict(
                    "event Boolean immutable transition differs".into(),
                ))
            }
            None => missing.push(expected),
        }
    }
    if existing.len() + missing.len() != prepared.product.transitions.len() {
        return Err(Error::Conflict(
            "unexpected event Boolean persisted row".into(),
        ));
    }
    Ok(missing)
}

impl ClickHouse {
    async fn read_event_boolean_rows(
        &self,
        hash: &str,
        header: &Header,
        require_complete: bool,
    ) -> Result<Vec<Row>> {
        if !hash_valid(hash) {
            return Err(Error::Invalid("event Boolean product hash".into()));
        }
        let mut rows = Vec::new();
        let mut after = None;
        loop {
            let cursor = after.map_or(String::new(), |index| format!(" AND event_index > {index}"));
            let sql = format!(
                "SELECT product_hash,provider,instrument,session,event_index,boundary_id,source_sequence,evaluated_at_ns,known,value FROM {}.{TRANSITIONS} WHERE product_hash='{hash}'{cursor} ORDER BY event_index LIMIT {} FORMAT JSONEachRow",
                self.database,
                PAGE + 1,
            );
            let mut page = decode_rows(
                &self.request(&sql, String::new()).await?,
                hash,
                header,
                after,
            )?;
            if page.is_empty() {
                break;
            }
            let has_more = page.len() > PAGE;
            if has_more {
                page.pop();
            }
            after = page.last().map(|row| row.event_index);
            rows.extend(page);
            if rows.len() > header.transition_count {
                return Err(Error::Conflict(
                    "unexpected event Boolean transition".into(),
                ));
            }
            if !has_more {
                break;
            }
        }
        if require_complete && rows.len() != header.transition_count {
            return Err(Error::Unready("event Boolean transitions missing".into()));
        }
        Ok(rows)
    }

    pub async fn publish_event_boolean_product(
        &self,
        prepared: &Prepared,
        now_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [
            Acceptance::RepositoryExtracted,
            Acceptance::SourceIdentity,
            Acceptance::EventStorage,
            Acceptance::StrategyParity,
            Acceptance::Durability,
        ] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "event Boolean publication acceptance missing: {required:?}"
                )));
            }
        }
        prepared
            .header
            .validate(&prepared.header.source_authority_hash, now_ns)?;
        if prepared.product.hash()? != prepared.hash {
            return Err(Error::Conflict(
                "event Boolean prepared product changed".into(),
            ));
        }
        lease.require(&event_boolean_publication_scope(prepared)?)?;
        self.verify_storage(TRANSITIONS).await?;
        self.verify_storage(PRODUCTS).await?;
        let existing = self
            .read_event_boolean_rows(&prepared.hash, &prepared.header, false)
            .await?;
        let missing = missing_rows(prepared, &existing)?;
        for chunk in missing.chunks(PAGE) {
            let rows: Vec<_> = chunk
                .iter()
                .map(|item| {
                    serde_json::to_value(item).map_err(|e| Error::Serialization(e.to_string()))
                })
                .collect::<Result<_>>()?;
            self.insert(TRANSITIONS, &rows).await?;
        }
        let readback = self
            .read_event_boolean_rows(&prepared.hash, &prepared.header, true)
            .await?;
        let expected: Vec<_> = prepared
            .product
            .transitions
            .iter()
            .map(|item| Row::from_transition(&prepared.hash, &prepared.product, item))
            .collect();
        if readback != expected {
            return Err(Error::Conflict(
                "event Boolean transition readback differs".into(),
            ));
        }
        let payload = serde_json::to_string(&prepared.header)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if let Some(existing) = self
            .immutable_value(PRODUCTS, "product_hash", &prepared.hash, "payload_json")
            .await?
        {
            if existing != payload {
                return Err(Error::Conflict(
                    "event Boolean product header differs".into(),
                ));
            }
        } else {
            self.insert(
                PRODUCTS,
                &[serde_json::json!({"product_hash":prepared.hash,"payload_json":payload})],
            )
            .await?;
        }
        self.read_event_boolean_product(
            &prepared.hash,
            &prepared.header.source_authority_hash,
            now_ns,
        )
        .await?;
        Ok(prepared.hash.clone())
    }

    pub async fn read_event_boolean_product(
        &self,
        expected_product_hash: &str,
        expected_source_authority_hash: &str,
        as_of_ns: u64,
    ) -> Result<Product> {
        if !hash_valid(expected_product_hash) || !hash_valid(expected_source_authority_hash) {
            return Err(Error::Invalid("event Boolean read identity".into()));
        }
        self.verify_storage(TRANSITIONS).await?;
        self.verify_storage(PRODUCTS).await?;
        let payload = self
            .immutable_value(
                PRODUCTS,
                "product_hash",
                expected_product_hash,
                "payload_json",
            )
            .await?
            .ok_or_else(|| Error::Unready("event Boolean product header missing".into()))?;
        let header: Header = serde_json::from_str(&payload)
            .map_err(|_| Error::Invalid("event Boolean product header JSON".into()))?;
        header.validate(expected_source_authority_hash, as_of_ns)?;
        let rows = self
            .read_event_boolean_rows(expected_product_hash, &header, true)
            .await?;
        let transitions = rows
            .iter()
            .map(|row| row.transition(expected_product_hash, &header))
            .collect::<Result<_>>()?;
        let product = header.product(transitions)?;
        if product.hash()? != expected_product_hash {
            return Err(Error::Conflict(
                "event Boolean product readback hash differs".into(),
            ));
        }
        Ok(product)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::execution_interval::ExecutableKind;

    fn definition() -> ExecutionContract {
        ExecutionContract {
            kind: ExecutableKind::SignalStream,
            id: "event-signal".into(),
            implementation_hash: "a".repeat(64),
            interval: ExecutionInterval::Events,
        }
    }
    fn product() -> Product {
        Product {
            provider: 1,
            instrument: 10,
            session: 20260922,
            interval: Interval {
                start: 100,
                end: 400,
            },
            definition_hash: definition().hash().unwrap(),
            event_count: 3,
            source_hash: "b".repeat(64),
            evaluation_hash: "c".repeat(64),
            transitions: vec![Transition {
                event_index: 1,
                boundary_id: "d".repeat(64),
                source_sequence: 2,
                evaluated_at_ns: 201,
                value: Some(true),
            }],
        }
    }
    fn source() -> ExpectedSource {
        ExpectedSource {
            authority_hash: "e".repeat(64),
            event_count: 3,
            source_hash: "b".repeat(64),
            certified_at_ns: 450,
        }
    }
    #[test]
    fn event_product_header_and_sparse_rows_reconstruct_exact_identity() {
        let prepared = prepare_from_source(product(), &definition(), &source(), 500).unwrap();
        let row = Row::from_transition(
            prepared.hash(),
            prepared.product(),
            &prepared.product.transitions[0],
        );
        let body = serde_json::to_string(&row).unwrap();
        let rows = decode_rows(&body, prepared.hash(), &prepared.header, None).unwrap();
        assert_eq!(rows, vec![row]);
        assert_eq!(missing_rows(&prepared, &[]).unwrap(), rows);
        assert!(missing_rows(&prepared, &rows).unwrap().is_empty());
        let recovered = prepared
            .header
            .clone()
            .product(vec![rows[0]
                .transition(prepared.hash(), &prepared.header)
                .unwrap()])
            .unwrap();
        assert_eq!(recovered, product());
        assert!(prepared.header.clone().product(vec![]).is_err());
        assert_eq!(recovered.hash().unwrap(), prepared.hash());
        assert_eq!(
            event_boolean_publication_scope(&prepared).unwrap().len(),
            64
        );
        assert!(prepared.header.validate(&"f".repeat(64), 500).is_err());
        assert!(prepared.header.validate(&"e".repeat(64), 499).is_err());
    }
    #[test]
    fn duplicate_foreign_and_changed_rows_fail_readback() {
        let prepared = prepare_from_source(product(), &definition(), &source(), 500).unwrap();
        let row = Row::from_transition(
            prepared.hash(),
            prepared.product(),
            &prepared.product.transitions[0],
        );
        let body = serde_json::to_string(&row).unwrap();
        assert!(decode_rows(
            &format!("{body}\n{body}"),
            prepared.hash(),
            &prepared.header,
            None
        )
        .is_err());
        assert!(missing_rows(&prepared, &[row.clone(), row.clone()]).is_err());
        let mut changed = row.clone();
        changed.value = 0;
        assert!(missing_rows(&prepared, &[changed.clone()]).is_err());
        let different = serde_json::to_string(&changed).unwrap();
        let changed_rows =
            decode_rows(&different, prepared.hash(), &prepared.header, None).unwrap();
        let reconstructed = prepared
            .header
            .clone()
            .product(vec![changed_rows[0]
                .transition(prepared.hash(), &prepared.header)
                .unwrap()])
            .unwrap();
        assert_ne!(reconstructed.hash().unwrap(), prepared.hash());
        changed.provider = 2;
        assert!(decode_rows(
            &serde_json::to_string(&changed).unwrap(),
            prepared.hash(),
            &prepared.header,
            None
        )
        .is_err());
        let mut fixed = definition();
        fixed.interval = ExecutionInterval::Fixed(100_000_000);
        assert!(prepare_from_source(product(), &fixed, &source(), 500).is_err());
        let mut wrong = source();
        wrong.event_count = 2;
        assert!(prepare_from_source(product(), &definition(), &wrong, 500).is_err());
        wrong = source();
        wrong.source_hash = "f".repeat(64);
        assert!(prepare_from_source(product(), &definition(), &wrong, 500).is_err());
        wrong = source();
        wrong.certified_at_ns = 501;
        assert!(prepare_from_source(product(), &definition(), &wrong, 500).is_err());
    }
}

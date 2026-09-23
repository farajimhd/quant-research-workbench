//! Deduplicated binary archive for one unpublished multi-ticker graph.
//! Hydration validates bytes and pins, not durability or recovery authority.
use super::*;
use std::collections::{BTreeMap, BTreeSet};

pub const CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_ROOT_BYTES: usize = 16 * 1024;
const MAGIC: &[u8; 8] = b"ARTEMR01";
const MAX_SHARDS: usize = 4096;
const MAX_OBJECTS: usize = 200_000;
const MAX_OVERHEAD: usize = 8 * 1024 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Header {
    pub version: u32,
    pub manifest: String,
    pub startup: String,
    pub cut: Cut,
    pub graph: String,
    pub archive: String,
    pub total_bytes: usize,
    pub chunks: Vec<String>,
}

pub struct Stored {
    pub root: Object,
    pub chunks: BTreeMap<String, Object>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Shape {
    version: u32,
    graph: String,
    portfolio: String,
    controllers: BTreeMap<u64, ControllerShape>,
    strategies: BTreeMap<u64, StrategyShape>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ControllerShape {
    root: String,
    accounts: String,
    playback: String,
    scheduler: String,
    market: String,
    trades: String,
    quotes: String,
    book: String,
    barrier: Option<String>,
    execution: String,
    execution_objects: Vec<String>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StrategyShape {
    root: String,
    accounts: BTreeMap<String, String>,
}

fn valid_hash(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn archive_limit(maximum: usize) -> Result<usize> {
    if maximum == 0 || maximum > 64 * CHUNK_BYTES {
        return Err(Error::Capacity("multi-run archive graph budget".into()));
    }
    maximum
        .checked_add(MAX_OVERHEAD)
        .ok_or_else(|| Error::Capacity("multi-run archive size overflow".into()))
}
fn insert(objects: &mut BTreeMap<String, Object>, object: &Object) -> Result<String> {
    object.verify()?;
    if object.payload.is_empty()
        || objects.len() >= MAX_OBJECTS && !objects.contains_key(&object.id)
    {
        return Err(Error::Capacity("multi-run archive object budget".into()));
    }
    if objects
        .insert(object.id.clone(), object.clone())
        .is_some_and(|previous| previous.payload != object.payload)
    {
        return Err(Error::Conflict("multi-run object hash collision".into()));
    }
    Ok(object.id.clone())
}

impl Header {
    pub fn decode(root: &Object, maximum_bytes: usize) -> Result<Self> {
        let maximum = archive_limit(maximum_bytes)?;
        root.verify()?;
        if root.payload.len() > MAX_ROOT_BYTES {
            return Err(Error::Capacity("multi-run archive header budget".into()));
        }
        let header: Self = serde_json::from_slice(&root.payload)
            .map_err(|error| Error::Serialization(error.to_string()))?;
        if header.version != 1
            || !valid_hash(&header.manifest)
            || !valid_hash(&header.startup)
            || !valid_hash(&header.cut.boundary_hash)
            || !valid_hash(&header.graph)
            || !valid_hash(&header.archive)
            || header.cut.boundary_sequence == 0
            || header.total_bytes < MAGIC.len() + 24
            || header.total_bytes > maximum
            || header.chunks.len() != header.total_bytes.div_ceil(CHUNK_BYTES)
            || header.chunks.iter().any(|id| !valid_hash(id))
            || serde_json::to_vec(&header)
                .map_err(|error| Error::Serialization(error.to_string()))?
                != root.payload
        {
            return Err(Error::Conflict("multi-run archive header differs".into()));
        }
        Ok(header)
    }
}

impl Stored {
    pub fn from_bundle(
        bundle: &Bundle,
        manifest: &Pinned,
        startup: &str,
        cut: &Cut,
        maximum_bytes: usize,
    ) -> Result<Self> {
        bundle.verify_pins(&bundle.root.id, manifest, startup, cut, maximum_bytes)?;
        if bundle.controllers.len() > MAX_SHARDS {
            return Err(Error::Capacity("multi-run archive shard budget".into()));
        }
        let mut objects = BTreeMap::new();
        let graph = insert(&mut objects, &bundle.root)?;
        let portfolio = insert(&mut objects, &bundle.portfolio)?;
        let mut controllers = BTreeMap::new();
        let mut strategies = BTreeMap::new();
        for (instrument, controller) in &bundle.controllers {
            let playback = &controller.playback.playback;
            let scheduler = &playback.scheduler;
            let mut execution_objects = Vec::new();
            for (key, object) in &controller.execution.objects {
                if key != &object.id {
                    return Err(Error::Conflict("multi-run execution object key".into()));
                }
                execution_objects.push(insert(&mut objects, object)?);
            }
            let shape = ControllerShape {
                root: insert(&mut objects, &controller.root)?,
                accounts: insert(&mut objects, &controller.playback.root)?,
                playback: insert(&mut objects, &playback.root)?,
                scheduler: insert(&mut objects, &scheduler.root)?,
                market: insert(&mut objects, &scheduler.market)?,
                trades: insert(&mut objects, &scheduler.trades)?,
                quotes: insert(&mut objects, &scheduler.quotes)?,
                book: insert(&mut objects, &scheduler.book)?,
                barrier: controller
                    .playback
                    .barrier
                    .as_ref()
                    .map(|object| insert(&mut objects, object))
                    .transpose()?,
                execution: insert(&mut objects, &controller.execution.root)?,
                execution_objects,
            };
            controllers.insert(*instrument, shape);
            let strategy = &bundle.strategies[instrument];
            let mut accounts = BTreeMap::new();
            for (key, object) in &strategy.accounts {
                if !valid_hash(key) {
                    return Err(Error::Invalid("multi-run strategy scope key".into()));
                }
                accounts.insert(key.clone(), insert(&mut objects, object)?);
            }
            strategies.insert(
                *instrument,
                StrategyShape {
                    root: insert(&mut objects, &strategy.root)?,
                    accounts,
                },
            );
        }
        let shape = Shape {
            version: 1,
            graph,
            portfolio,
            controllers,
            strategies,
        };
        let shape_bytes =
            serde_json::to_vec(&shape).map_err(|error| Error::Serialization(error.to_string()))?;
        let maximum = archive_limit(maximum_bytes)?;
        if shape_bytes.len() > MAX_OVERHEAD || objects.len() > MAX_OBJECTS {
            return Err(Error::Capacity("multi-run archive shape budget".into()));
        }
        let mut bytes = Vec::new();
        bytes.extend_from_slice(MAGIC);
        bytes.extend_from_slice(&(shape_bytes.len() as u64).to_le_bytes());
        bytes.extend_from_slice(&shape_bytes);
        bytes.extend_from_slice(&(objects.len() as u64).to_le_bytes());
        for (id, object) in objects {
            let next = bytes
                .len()
                .checked_add(64 + 8)
                .and_then(|size| size.checked_add(object.payload.len()))
                .ok_or_else(|| Error::Capacity("multi-run archive size overflow".into()))?;
            if next > maximum {
                return Err(Error::Capacity("multi-run archive byte budget".into()));
            }
            bytes.extend_from_slice(id.as_bytes());
            bytes.extend_from_slice(&(object.payload.len() as u64).to_le_bytes());
            bytes.extend_from_slice(&object.payload);
        }
        let archive = Object::new(bytes);
        let mut chunks = BTreeMap::new();
        let ids = archive
            .payload
            .chunks(CHUNK_BYTES)
            .map(|part| {
                let chunk = Object::new(part.to_vec());
                let id = chunk.id.clone();
                chunks.insert(id.clone(), chunk);
                id
            })
            .collect();
        let root = Object::new(
            serde_json::to_vec(&Header {
                version: 1,
                manifest: manifest.hash().into(),
                startup: startup.into(),
                cut: cut.clone(),
                graph: bundle.root.id.clone(),
                archive: archive.id,
                total_bytes: archive.payload.len(),
                chunks: ids,
            })
            .map_err(|error| Error::Serialization(error.to_string()))?,
        );
        Header::decode(&root, maximum_bytes)?;
        Ok(Self { root, chunks })
    }

    pub fn hydrate(
        &self,
        manifest: &Pinned,
        startup: &str,
        cut: &Cut,
        expected_graph: &str,
        maximum_bytes: usize,
    ) -> Result<Bundle> {
        let header = Header::decode(&self.root, maximum_bytes)?;
        if header.manifest != manifest.hash()
            || header.startup != startup
            || header.cut != *cut
            || header.graph != expected_graph
            || header.chunks.iter().collect::<BTreeSet<_>>()
                != self.chunks.keys().collect::<BTreeSet<_>>()
        {
            return Err(Error::Conflict(
                "multi-run archive context or chunks differ".into(),
            ));
        }
        let mut bytes = Vec::with_capacity(header.total_bytes);
        for (index, id) in header.chunks.iter().enumerate() {
            let chunk = &self.chunks[id];
            chunk.verify()?;
            if chunk.id != *id
                || chunk.payload.len()
                    != (header.total_bytes - index * CHUNK_BYTES).min(CHUNK_BYTES)
            {
                return Err(Error::Conflict("multi-run archive chunk differs".into()));
            }
            bytes.extend_from_slice(&chunk.payload);
        }
        let archive = Object {
            id: header.archive,
            payload: bytes,
        };
        archive.verify()?;
        unpack(
            &archive.payload,
            manifest,
            startup,
            cut,
            expected_graph,
            maximum_bytes,
        )
    }
}

struct Reader<'a> {
    bytes: &'a [u8],
}
impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        let part = self
            .bytes
            .get(..n)
            .ok_or_else(|| Error::Invalid("truncated multi-run archive".into()))?;
        self.bytes = &self.bytes[n..];
        Ok(part)
    }
    fn length(&mut self) -> Result<usize> {
        let raw: [u8; 8] = self.take(8)?.try_into().unwrap();
        usize::try_from(u64::from_le_bytes(raw))
            .map_err(|_| Error::Capacity("multi-run archive length".into()))
    }
}

fn unpack(
    bytes: &[u8],
    manifest: &Pinned,
    startup: &str,
    cut: &Cut,
    expected_graph: &str,
    maximum_bytes: usize,
) -> Result<Bundle> {
    if bytes.len() > archive_limit(maximum_bytes)? {
        return Err(Error::Capacity("multi-run archive byte budget".into()));
    }
    let mut reader = Reader { bytes };
    if reader.take(MAGIC.len())? != MAGIC {
        return Err(Error::Invalid("multi-run archive version".into()));
    }
    let shape_len = reader.length()?;
    if shape_len == 0 || shape_len > MAX_OVERHEAD {
        return Err(Error::Capacity("multi-run archive shape budget".into()));
    }
    let shape_bytes = reader.take(shape_len)?;
    let shape: Shape = serde_json::from_slice(shape_bytes)
        .map_err(|error| Error::Serialization(error.to_string()))?;
    if shape.version != 1
        || shape.graph != expected_graph
        || shape.controllers.is_empty()
        || shape.controllers.len() > MAX_SHARDS
        || !shape.controllers.keys().eq(shape.strategies.keys())
        || serde_json::to_vec(&shape).map_err(|error| Error::Serialization(error.to_string()))?
            != shape_bytes
    {
        return Err(Error::Conflict("multi-run archive shape differs".into()));
    }
    let count = reader.length()?;
    if count == 0 || count > MAX_OBJECTS {
        return Err(Error::Capacity("multi-run archive object count".into()));
    }
    let mut objects = BTreeMap::new();
    let mut used_bytes = 0usize;
    for _ in 0..count {
        let id = std::str::from_utf8(reader.take(64)?)
            .map_err(|_| Error::Invalid("multi-run object id encoding".into()))?
            .to_owned();
        let length = reader.length()?;
        used_bytes = used_bytes
            .checked_add(length)
            .ok_or_else(|| Error::Capacity("multi-run object size overflow".into()))?;
        if !valid_hash(&id)
            || length == 0
            || used_bytes > maximum_bytes
            || objects
                .last_key_value()
                .is_some_and(|(last, _)| last >= &id)
        {
            return Err(Error::Conflict("multi-run object order or budget".into()));
        }
        let object = Object {
            id: id.clone(),
            payload: reader.take(length)?.to_vec(),
        };
        object.verify()?;
        objects.insert(id, object);
    }
    if !reader.bytes.is_empty() {
        return Err(Error::Invalid("multi-run archive trailing bytes".into()));
    }
    let mut referenced = BTreeSet::new();
    let mut get = |id: &str| -> Result<Object> {
        referenced.insert(id.to_owned());
        objects
            .get(id)
            .cloned()
            .ok_or_else(|| Error::Unready("multi-run object missing".into()))
    };
    let root = get(&shape.graph)?;
    let portfolio = get(&shape.portfolio)?;
    let mut controllers = BTreeMap::new();
    let mut strategies = BTreeMap::new();
    for (instrument, row) in &shape.controllers {
        if row
            .execution_objects
            .windows(2)
            .any(|pair| pair[0] >= pair[1])
        {
            return Err(Error::Conflict("multi-run execution object order".into()));
        }
        let mut execution_objects = BTreeMap::new();
        for id in &row.execution_objects {
            execution_objects.insert(id.clone(), get(id)?);
        }
        let controller = crate::playback_runtime::checkpoint::Bundle {
            root: get(&row.root)?,
            execution: crate::simulation_runtime::checkpoint::Bundle {
                root: get(&row.execution)?,
                objects: execution_objects,
            },
            playback:
                arte_core::market_structure::scheduler::playback::accounts::checkpoint::Bundle {
                    root: get(&row.accounts)?,
                    barrier: row.barrier.as_ref().map(|id| get(id)).transpose()?,
                    playback:
                        arte_core::market_structure::scheduler::playback::checkpoint::Bundle {
                            root: get(&row.playback)?,
                            scheduler: arte_core::market_structure::scheduler::checkpoint::Bundle {
                                root: get(&row.scheduler)?,
                                market: get(&row.market)?,
                                trades: get(&row.trades)?,
                                quotes: get(&row.quotes)?,
                                book: get(&row.book)?,
                            },
                        },
                },
        };
        controllers.insert(*instrument, controller);
        let row = &shape.strategies[instrument];
        let mut accounts = BTreeMap::new();
        for (key, id) in &row.accounts {
            if !valid_hash(key) {
                return Err(Error::Invalid("multi-run strategy key".into()));
            }
            accounts.insert(key.clone(), get(id)?);
        }
        strategies.insert(
            *instrument,
            crate::playback_runtime::strategy350_accounts::checkpoint::Bundle {
                root: get(&row.root)?,
                accounts,
            },
        );
    }
    if referenced != objects.keys().cloned().collect() {
        return Err(Error::Conflict("multi-run archive surplus object".into()));
    }
    let bundle = Bundle {
        root,
        controllers,
        strategies,
        portfolio,
    };
    bundle.verify_pins(expected_graph, manifest, startup, cut, maximum_bytes)?;
    Ok(bundle)
}

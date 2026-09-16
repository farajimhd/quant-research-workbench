//! Binary frames avoid JSON byte-array expansion. The small publication header
//! pins the complete archive; hydration is not permission to resume a run.
use super::*;
use std::collections::BTreeSet;
pub const CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_ROOT_BYTES: usize = 16 * 1024;
const MAGIC: &[u8; 8] = b"ARTERU01";
const MAX_EXECUTION: usize = 200_002;
const MAX_CANDIDATES: usize = 4096;
const OVERHEAD: usize = 24 + (14 + MAX_EXECUTION + MAX_CANDIDATES) * 8 + MAX_CANDIDATES * 64;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Header {
    pub version: u32,
    pub manifest: String,
    pub cut: Cut,
    pub checkpoint: String,
    pub archive: String,
    pub total_bytes: usize,
    pub chunks: Vec<String>,
}
pub struct Stored {
    pub root: Object,
    pub chunks: BTreeMap<String, Object>,
}
fn hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn maximum(bytes: usize) -> Result<usize> {
    if bytes == 0 || bytes > 64 * CHUNK_BYTES {
        return Err(Error::Capacity("run archive payload budget".into()));
    }
    Ok(bytes + OVERHEAD)
}
impl Header {
    pub fn decode(object: &Object, maximum_bytes: usize) -> Result<Self> {
        let maximum = maximum(maximum_bytes)?;
        if object.payload.len() > MAX_ROOT_BYTES {
            return Err(Error::Capacity("run archive header budget".into()));
        }
        object.verify()?;
        let header: Self = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if header.version != 1
            || !hash(&header.manifest)
            || !hash(&header.checkpoint)
            || !hash(&header.archive)
            || !hash(&header.cut.boundary_hash)
            || header.cut.boundary_sequence == 0
            || header.total_bytes < 24
            || header.total_bytes > maximum
            || header.chunks.len() != header.total_bytes.div_ceil(CHUNK_BYTES)
            || header.chunks.iter().any(|s| !hash(s))
            || serde_json::to_vec(&header).map_err(|e| Error::Serialization(e.to_string()))?
                != object.payload
        {
            return Err(Error::Invalid(
                "run archive header invalid or noncanonical".into(),
            ));
        }
        Ok(header)
    }
}
impl Stored {
    pub fn from_bundle(bundle: &Bundle, maximum_bytes: usize) -> Result<Self> {
        maximum(maximum_bytes)?;
        bundle.require_size(maximum_bytes)?;
        if bundle.controller.execution.objects.len() > MAX_EXECUTION
            || bundle.candidates.candidates.len() > MAX_CANDIDATES
        {
            return Err(Error::Capacity("run archive object count".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(MAGIC);
        bytes.extend_from_slice(&(bundle.controller.execution.objects.len() as u64).to_le_bytes());
        bytes.extend_from_slice(&(bundle.candidates.candidates.len() as u64).to_le_bytes());
        for object in [
            &bundle.root,
            &bundle.portfolio,
            &bundle.candidates.root,
            &bundle.candidates.features,
        ]
        .into_iter()
        .chain(controller_objects(&bundle.controller))
        {
            append(&mut bytes, object)?;
        }
        // controller_objects includes execution children. Their keys are hashes;
        // no duplicated identifiers need to be written to the archive.
        if bundle.controller.playback.barrier.is_none() {
            return Err(Error::Unready(
                "run archive requires pending barrier".into(),
            ));
        }
        for (key, object) in &bundle.controller.execution.objects {
            if key != &object.id {
                return Err(Error::Conflict("run archive execution key".into()));
            }
        }
        for (key, object) in &bundle.candidates.candidates {
            if !hash(key) {
                return Err(Error::Invalid("run archive candidate key".into()));
            }
            bytes.extend_from_slice(key.as_bytes());
            append(&mut bytes, object)?;
        }
        if bytes.len() > maximum(maximum_bytes)? {
            return Err(Error::Capacity("run archive bytes".into()));
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
                manifest: root.manifest,
                cut: root.cut,
                checkpoint: bundle.root.id.clone(),
                archive: archive.id,
                total_bytes: archive.payload.len(),
                chunks: ids,
            })
            .map_err(|e| Error::Serialization(e.to_string()))?,
        );
        Header::decode(&root, maximum_bytes)?;
        Ok(Self { root, chunks })
    }
    pub fn hydrate(&self, maximum_bytes: usize) -> Result<Bundle> {
        let header = Header::decode(&self.root, maximum_bytes)?;
        if header.chunks.iter().collect::<BTreeSet<_>>()
            != self.chunks.keys().collect::<BTreeSet<_>>()
        {
            return Err(Error::Unready("run archive chunk set".into()));
        }
        let mut bytes = Vec::with_capacity(header.total_bytes);
        for (index, id) in header.chunks.iter().enumerate() {
            let chunk = &self.chunks[id];
            chunk.verify()?;
            if &chunk.id != id
                || chunk.payload.len()
                    != (header.total_bytes - index * CHUNK_BYTES).min(CHUNK_BYTES)
            {
                return Err(Error::Conflict("run archive chunk length or id".into()));
            }
            bytes.extend_from_slice(&chunk.payload);
        }
        let archive = Object {
            id: header.archive.clone(),
            payload: bytes,
        };
        archive.verify()?;
        unpack(&archive.payload, &header, maximum_bytes)
    }
}
fn append(bytes: &mut Vec<u8>, object: &Object) -> Result<()> {
    object.verify()?;
    if object.payload.is_empty() {
        return Err(Error::Invalid("empty run archive object".into()));
    }
    bytes.extend_from_slice(&(object.payload.len() as u64).to_le_bytes());
    bytes.extend_from_slice(&object.payload);
    Ok(())
}
struct Reader<'a> {
    bytes: &'a [u8],
    left: usize,
}
impl Reader<'_> {
    fn length(&mut self) -> Result<usize> {
        let raw = self
            .bytes
            .get(..8)
            .ok_or_else(|| Error::Invalid("truncated run archive".into()))?;
        let n = u64::from_le_bytes(raw.try_into().unwrap());
        self.bytes = &self.bytes[8..];
        usize::try_from(n).map_err(|_| Error::Capacity("run archive length".into()))
    }
    fn object(&mut self) -> Result<Object> {
        let n = self.length()?;
        if n == 0 || n > self.left || n > self.bytes.len() {
            return Err(Error::Capacity("run archive object length".into()));
        }
        let object = Object::new(self.bytes[..n].to_vec());
        self.bytes = &self.bytes[n..];
        self.left -= n;
        Ok(object)
    }
}
fn unpack(bytes: &[u8], header: &Header, maximum_bytes: usize) -> Result<Bundle> {
    use arte_core::market_structure::scheduler::{
        checkpoint as scheduler,
        playback::{accounts::checkpoint as accounts, checkpoint as playback},
    };
    if bytes.get(..8) != Some(MAGIC.as_slice()) {
        return Err(Error::Invalid("run archive version".into()));
    }
    let mut reader = Reader {
        bytes: &bytes[8..],
        left: maximum_bytes,
    };
    let execution_count = reader.length()?;
    let candidate_count = reader.length()?;
    if execution_count > MAX_EXECUTION || candidate_count > MAX_CANDIDATES {
        return Err(Error::Capacity("run archive object count".into()));
    }
    let root = reader.object()?;
    let portfolio = reader.object()?;
    let candidate_root = reader.object()?;
    let features = reader.object()?;
    let controller_root = reader.object()?;
    let execution_root = reader.object()?;
    let accounts_root = reader.object()?;
    let playback_root = reader.object()?;
    let scheduler = scheduler::Bundle {
        root: reader.object()?,
        market: reader.object()?,
        trades: reader.object()?,
        quotes: reader.object()?,
        book: reader.object()?,
    };
    let barrier = Some(reader.object()?);
    let mut objects = BTreeMap::new();
    for _ in 0..execution_count {
        let object = reader.object()?;
        if objects
            .last_key_value()
            .is_some_and(|(id, _)| id >= &object.id)
        {
            return Err(Error::Conflict("run archive execution ordering".into()));
        }
        objects.insert(object.id.clone(), object);
    }
    let mut candidates = BTreeMap::new();
    for _ in 0..candidate_count {
        let key = std::str::from_utf8(
            reader
                .bytes
                .get(..64)
                .ok_or_else(|| Error::Invalid("truncated candidate key".into()))?,
        )
        .map_err(|_| Error::Invalid("candidate key encoding".into()))?
        .to_owned();
        reader.bytes = &reader.bytes[64..];
        if !hash(&key)
            || candidates
                .last_key_value()
                .is_some_and(|(id, _)| id >= &key)
        {
            return Err(Error::Conflict("run archive candidate ordering".into()));
        }
        candidates.insert(key, reader.object()?);
    }
    if !reader.bytes.is_empty() {
        return Err(Error::Invalid("run archive trailing bytes".into()));
    }
    let context: Root =
        serde_json::from_slice(&root.payload).map_err(|e| Error::Serialization(e.to_string()))?;
    if root.id != header.checkpoint
        || context.manifest != header.manifest
        || context.cut != header.cut
    {
        return Err(Error::Conflict("run archive context differs".into()));
    }
    Ok(Bundle {
        root,
        portfolio,
        controller: checkpoint::Bundle {
            root: controller_root,
            execution: simulation_runtime::checkpoint::Bundle {
                root: execution_root,
                objects,
            },
            playback: accounts::Bundle {
                root: accounts_root,
                barrier,
                playback: playback::Bundle {
                    root: playback_root,
                    scheduler,
                },
            },
        },
        candidates: candidates::checkpoint::Bundle {
            root: candidate_root,
            features,
            candidates,
        },
    })
}

//! Length-framed object graph split into bounded database chunks. Object hashes
//! are reconstructed from bytes; the archive does not duplicate their payloads.
use super::*;
pub const CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_ROOT_BYTES: usize = 16 * 1024;
const MAGIC: &[u8; 8] = b"ARTEEX01";
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Header {
    pub version: u32,
    pub manifest_hash: String,
    pub cut: Cut,
    pub instrument: u64,
    pub execution_root: String,
    pub archive_hash: String,
    pub total_bytes: usize,
    pub chunks: Vec<String>,
}
pub struct Stored {
    pub root: Object,
    pub chunks: BTreeMap<String, Object>,
}
fn valid_hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn archive_limit(limits: Limits) -> Result<usize> {
    if limits.maximum_bytes == 0
        || limits.maximum_bytes > 64 * CHUNK_BYTES
        || limits.maximum_orders == 0
        || limits.maximum_orders > 100_000
    {
        return Err(Error::Invalid("execution archive limits".into()));
    }
    Ok(limits.maximum_bytes + (limits.maximum_orders * 2 + 3) * 8 + 16)
}
impl Header {
    pub fn decode(object: &Object, limits: Limits) -> Result<Self> {
        if object.payload.len() > MAX_ROOT_BYTES {
            return Err(Error::Capacity("execution storage root budget".into()));
        }
        object.verify()?;
        let header: Self = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if header.version != 1
            || !valid_hash(&header.manifest_hash)
            || !valid_hash(&header.execution_root)
            || !valid_hash(&header.archive_hash)
            || !valid_hash(&header.cut.boundary_hash)
            || header.cut.boundary_sequence == 0
            || header.instrument == 0
            || header.total_bytes < 16
            || header.total_bytes > archive_limit(limits)?
            || header.chunks.len() != header.total_bytes.div_ceil(CHUNK_BYTES)
            || header.chunks.iter().any(|id| !valid_hash(id))
            || serde_json::to_vec(&header).map_err(|e| Error::Serialization(e.to_string()))?
                != object.payload
        {
            return Err(Error::Invalid(
                "execution storage header invalid or noncanonical".into(),
            ));
        }
        Ok(header)
    }
}
impl Stored {
    pub fn from_execution(bundle: &Bundle, limits: Limits) -> Result<Self> {
        let maximum = archive_limit(limits)?;
        if bundle.objects.len() > limits.maximum_orders * 2 + 2 {
            return Err(Error::Capacity("execution archive object budget".into()));
        }
        let total = bundle
            .objects
            .values()
            .try_fold(bundle.root.payload.len(), |n, o| {
                n.checked_add(o.payload.len())
            })
            .ok_or_else(|| Error::Capacity("execution archive length overflow".into()))?;
        if total > limits.maximum_bytes {
            return Err(Error::Capacity("execution archive payload budget".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut bytes = Vec::with_capacity(total + (bundle.objects.len() + 1) * 8 + 16);
        bytes.extend_from_slice(MAGIC);
        bytes.extend_from_slice(&(bundle.objects.len() as u64).to_le_bytes());
        let mut append = |object: &Object| -> Result<()> {
            object.verify()?;
            bytes.extend_from_slice(&(object.payload.len() as u64).to_le_bytes());
            bytes.extend_from_slice(&object.payload);
            Ok(())
        };
        append(&bundle.root)?;
        for (id, object) in &bundle.objects {
            if id != &object.id {
                return Err(Error::Conflict(
                    "execution archive object key differs".into(),
                ));
            }
            append(object)?;
        }
        if bytes.len() > maximum {
            return Err(Error::Capacity("execution archive byte budget".into()));
        }
        let archive = Object::new(bytes);
        let mut chunks = BTreeMap::new();
        let mut ids = Vec::new();
        for bytes in archive.payload.chunks(CHUNK_BYTES) {
            let chunk = Object::new(bytes.to_vec());
            ids.push(chunk.id.clone());
            chunks.insert(chunk.id.clone(), chunk);
        }
        let header = Header {
            version: 1,
            manifest_hash: root.manifest_hash,
            cut: root.cut,
            instrument: root.source.1,
            execution_root: bundle.root.id.clone(),
            archive_hash: archive.id,
            total_bytes: archive.payload.len(),
            chunks: ids,
        };
        let root = Object::new(
            serde_json::to_vec(&header).map_err(|e| Error::Serialization(e.to_string()))?,
        );
        Header::decode(&root, limits)?;
        Ok(Self { root, chunks })
    }
    pub fn hydrate(&self, limits: Limits) -> Result<Bundle> {
        let header = Header::decode(&self.root, limits)?;
        if header.chunks.iter().collect::<BTreeSet<_>>()
            != self.chunks.keys().collect::<BTreeSet<_>>()
        {
            return Err(Error::Unready("execution archive chunk set differs".into()));
        }
        let mut bytes = Vec::with_capacity(header.total_bytes);
        for (index, id) in header.chunks.iter().enumerate() {
            let chunk = &self.chunks[id];
            chunk.verify()?;
            if &chunk.id != id
                || chunk.payload.len()
                    != (header.total_bytes - index * CHUNK_BYTES).min(CHUNK_BYTES)
            {
                return Err(Error::Conflict(
                    "execution archive chunk identity or size".into(),
                ));
            }
            bytes.extend_from_slice(&chunk.payload);
        }
        let archive = Object {
            id: header.archive_hash.clone(),
            payload: bytes,
        };
        archive.verify()?;
        unpack(&archive.payload, &header, limits)
    }
}
fn unpack(bytes: &[u8], header: &Header, limits: Limits) -> Result<Bundle> {
    if bytes.get(..8) != Some(MAGIC.as_slice()) {
        return Err(Error::Invalid("execution archive version".into()));
    }
    let mut remaining = &bytes[8..];
    fn length(remaining: &mut &[u8]) -> Result<usize> {
        let raw: [u8; 8] = remaining
            .get(..8)
            .ok_or_else(|| Error::Invalid("truncated execution archive".into()))?
            .try_into()
            .unwrap();
        *remaining = &remaining[8..];
        usize::try_from(u64::from_le_bytes(raw))
            .map_err(|_| Error::Capacity("execution archive length".into()))
    }
    let count = length(&mut remaining)?;
    if count > limits.maximum_orders * 2 + 2 {
        return Err(Error::Capacity("execution archive object count".into()));
    }
    let mut used = 0usize;
    let mut next = || -> Result<Object> {
        let len = length(&mut remaining)?;
        if len == 0 || len > limits.maximum_bytes.saturating_sub(used) || len > remaining.len() {
            return Err(Error::Capacity("execution archive frame length".into()));
        }
        let object = Object::new(remaining[..len].to_vec());
        remaining = &remaining[len..];
        used += len;
        Ok(object)
    };
    let root = next()?;
    if root.id != header.execution_root {
        return Err(Error::Conflict("execution archive root differs".into()));
    }
    let mut objects = BTreeMap::new();
    let mut previous = None;
    for _ in 0..count {
        let object = next()?;
        if previous.as_ref().is_some_and(|p| p >= &object.id) {
            return Err(Error::Conflict("execution archive object order".into()));
        }
        previous = Some(object.id.clone());
        objects.insert(object.id.clone(), object);
    }
    if !remaining.is_empty() {
        return Err(Error::Invalid("execution archive trailing bytes".into()));
    }
    let execution: Root =
        serde_json::from_slice(&root.payload).map_err(|e| Error::Serialization(e.to_string()))?;
    if execution.manifest_hash != header.manifest_hash
        || execution.cut != header.cut
        || execution.source.1 != header.instrument
    {
        return Err(Error::Conflict(
            "execution archive header context differs".into(),
        ));
    }
    Ok(Bundle { root, objects })
}

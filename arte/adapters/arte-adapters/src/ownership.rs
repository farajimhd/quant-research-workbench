//! Cooperative single-host ownership. Not a distributed lease or failover fence.
use arte_core::{Error, Result};
use std::{
    fs::{File, OpenOptions},
    path::Path,
};
pub(crate) fn job_hash(name: &str, plan: &str) -> Result<String> {
    if name.is_empty()
        || name.len() > 128
        || !name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        || plan.len() != 64
        || !plan
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::Invalid("invalid acquisition job identity".into()));
    }
    arte_core::content_hash(&("rest-acquisition-job-v1", name, plan))
}
pub struct Lease {
    file: File,
    scope: String,
}
impl Lease {
    /// lock_directory must be a pre-existing, approved external runtime directory.
    /// Every cooperating copy must use the same directory. Never unlink lease files:
    /// their stable identity is needed even after an owner exits. No file is truncated.
    pub fn acquire(lock_directory: &Path, scope: &str) -> Result<Self> {
        if !lock_directory.is_absolute()
            || scope.len() != 64
            || !scope
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid(
                "invalid ownership directory or scope".into(),
            ));
        }
        let directory = lock_directory
            .canonicalize()
            .map_err(|_| Error::Unready("ownership directory unavailable".into()))?;
        if !directory.is_dir() {
            return Err(Error::Invalid("ownership path is not a directory".into()));
        }
        let path = directory.join(format!("{scope}.lock"));
        if std::fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(Error::Invalid(
                "ownership lock must not be a symlink".into(),
            ));
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|_| Error::Unready("cannot open ownership lock".into()))?;
        if path
            .canonicalize()
            .map_err(|_| Error::Unready("cannot resolve ownership lock".into()))?
            .parent()
            != Some(directory.as_path())
        {
            return Err(Error::Invalid(
                "ownership lock left configured directory".into(),
            ));
        }
        file.try_lock().map_err(|_| {
            Error::Unready("job already owned or filesystem locking unavailable".into())
        })?;
        Ok(Self {
            file,
            scope: scope.to_owned(),
        })
    }
    pub fn require(&self, scope: &str) -> Result<()> {
        if scope != self.scope {
            return Err(Error::Conflict("ownership scope mismatch".into()));
        }
        self.file
            .metadata()
            .map_err(|_| Error::Unready("ownership handle unavailable".into()))?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_lock_excludes_second_handle_and_releases_on_drop() {
        let target = std::env::var_os("CARGO_TARGET_DIR")
            .expect("offline tests require explicit external CARGO_TARGET_DIR");
        let directory = std::path::PathBuf::from(target)
            .join("ownership-tests")
            .join(std::process::id().to_string());
        assert!(directory.is_absolute());
        std::fs::create_dir_all(&directory).unwrap();
        let scope = "a".repeat(64);
        let lease = Lease::acquire(&directory, &scope).unwrap();
        assert!(Lease::acquire(&directory, &scope).is_err());
        lease.require(&scope).unwrap();
        assert!(lease.require(&"b".repeat(64)).is_err());
        drop(lease);
        Lease::acquire(&directory, &scope).unwrap();
        // Leave the stable empty lock file in external test output; never unlink it.
    }
    #[test]
    fn rejects_relative_path_and_invalid_scope_without_creating_files() {
        assert!(Lease::acquire(Path::new("relative"), &"a".repeat(64)).is_err());
        assert!(job_hash("../job", &"a".repeat(64)).is_err());
    }
}

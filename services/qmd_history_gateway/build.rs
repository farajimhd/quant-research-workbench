use sha2::{Digest, Sha256};
use std::{fs, path::{Path, PathBuf}};

fn collect(dir: &Path, files: &mut Vec<PathBuf>) {
    println!("cargo:rerun-if-changed={}", dir.display());
    for entry in fs::read_dir(dir).expect("source directory") {
        let path = entry.expect("source entry").path();
        if path.is_dir() { collect(&path, files); }
        else if path.extension().is_some_and(|ext| ext == "rs") { files.push(path); }
    }
}

fn main() {
    let services = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap())
        .parent().unwrap().to_path_buf();
    let mut files = Vec::new();
    for name in ["qmd-gateway", "qmd_history_gateway"] {
        collect(&services.join(name).join("src"), &mut files);
        for filename in ["Cargo.toml", "Cargo.lock"] {
            let path = services.join(name).join(filename);
            if path.is_file() { files.push(path); }
        }
    }
    files.push(services.join("qmd_history_gateway/build.rs"));
    // Match the backend's normalized relative-path ordering, not PathBuf's
    // component ordering (foo/bar.rs versus foo.rs otherwise sorts differently).
    files.sort_by_key(|path| path.strip_prefix(&services).unwrap()
        .to_string_lossy().replace('\\', "/"));
    let mut hash = Sha256::new();
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        hash.update(path.strip_prefix(&services).unwrap().to_string_lossy().replace('\\', "/"));
        hash.update(b"\n");
        // Normalize checkout line endings so a Git CRLF conversion does not
        // masquerade as a different implementation.
        hash.update(fs::read_to_string(&path).expect("source text").replace("\r\n", "\n"));
        hash.update(b"\0");
    }
    println!("cargo:rustc-env=QMD_HISTORY_SOURCE_SHA256={:x}", hash.finalize());
}

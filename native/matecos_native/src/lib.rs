//! matecos_native
//! ==============
//! High-performance native Rust module for MATECOS.
//!
//! Exposes:
//! - `fast_index_repo`: Zero-GIL parallel repository filesystem traversal using `rayon` and `walkdir`.

use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;
use std::path::Path;
use walkdir::WalkDir;

#[pyclass]
#[derive(Clone)]
pub struct RustRepoIndex {
    #[pyo3(get)]
    pub total_files: usize,
    #[pyo3(get)]
    pub total_bytes: u64,
    #[pyo3(get)]
    pub files: Vec<(String, u64, String, String)>, // (rel_path, size_bytes, extension, language)
}

fn get_extension_language(ext: &str) -> &'static str {
    match ext {
        ".py" => "Python",
        ".js" | ".jsx" => "JavaScript",
        ".ts" | ".tsx" => "TypeScript",
        ".rs" => "Rust",
        ".go" => "Go",
        ".java" => "Java",
        ".c" | ".h" => "C",
        ".cpp" | ".hpp" => "C++",
        ".rb" => "Ruby",
        ".sh" => "Shell",
        ".json" => "JSON",
        ".yaml" | ".yml" => "YAML",
        ".toml" => "TOML",
        ".md" => "Markdown",
        _ => "",
    }
}

/// Fast parallel repository indexer releasing the Python GIL
#[pyfunction]
fn fast_index_repo(py: Python<'_>, root_path: &str) -> PyResult<RustRepoIndex> {
    let root = Path::new(root_path);
    let ignored_names: HashSet<&'static str> = [
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        ".pytest_cache", ".mypy_cache", "target", "dist", "build",
    ].into_iter().collect();

    // Release GIL for multi-threaded traversal
    let (total_files, total_bytes, files) = py.allow_threads(|| {
        let entries: Vec<_> = WalkDir::new(root)
            .into_iter()
            .filter_entry(|e| {
                if let Some(file_name) = e.file_name().to_str() {
                    !ignored_names.contains(file_name) && !file_name.starts_with('.')
                } else {
                    false
                }
            })
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().is_file())
            .collect();

        let total_files = entries.len();

        let files: Vec<(String, u64, String, String)> = entries
            .par_iter()
            .map(|entry| {
                let path = entry.path();
                let rel_path = path.strip_prefix(root).unwrap_or(path).to_string_lossy().into_owned();
                let size_bytes = entry.metadata().map(|m| m.len()).unwrap_or(0);
                let ext = path.extension().and_then(|s| s.to_str()).map(|e| format!(".{}", e.to_lowercase())).unwrap_or_default();
                let lang = get_extension_language(&ext).to_string();
                (rel_path, size_bytes, ext, lang)
            })
            .collect();

        let total_bytes: u64 = files.iter().map(|(_, sz, _, _)| *sz).sum();

        (total_files, total_bytes, files)
    });

    Ok(RustRepoIndex {
        total_files,
        total_bytes,
        files,
    })
}

#[pymodule]
fn matecos_native(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustRepoIndex>()?;
    m.add_function(wrap_pyfunction!(fast_index_repo, m)?)?;
    Ok(())
}

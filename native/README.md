# MATECOS Native Extensions (Rust / PyO3)

This directory contains optional native Rust extensions for performance-critical bottlenecks in MATECOS:
- **`fast_index_repo`**: Multi-threaded parallel filesystem traversal releasing the Python GIL using `rayon` and `walkdir`.

## Building the Native Extension

1. Ensure the Rust toolchain is installed:
   ```powershell
   winget install Rustlang.Rustup
   # or visit https://rustup.rs
   ```

2. Compile and link into the active Python environment:
   ```powershell
   maturin develop --release --manifest-path native/matecos_native/Cargo.toml
   ```

3. Verification:
   ```python
   import matecos_native
   res = matecos_native.fast_index_repo(".")
   print(f"Scanned {res.total_files} files ({res.total_bytes} bytes)")
   ```

## Graceful Fallback Invariant
MATECOS works immediately without compiling this crate:
- If `matecos_native` is compiled and available, `FastRepositoryIndexer` uses it for zero-GIL multi-core speedups.
- If not compiled, MATECOS uses the optimized multi-threaded Python worker in `src/tools/github_adapter/fast_indexer.py`.

# Packaging: Tauri shell, Python sidecar

**Written before the first build.** Sizes and outcomes are appended below the
line; nothing above it was revised afterwards.

## Shape

| piece | what | why |
|---|---|---|
| Shell | Tauri 2 | Native webview, no bundled Chromium. A planning grid is a table; it does not need 150 MB of browser. |
| Backend | Python, PyInstaller one-dir sidecar | The engines already exist and are the product. Rewriting `netreq` in Rust would be re-deriving verified arithmetic for no gain. |
| Store | SQLite | Single file, no service, no port, nothing for a user to install or a firewall to notice. PostgreSQL stays the option for a hosted tier. |
| Installers | MSI (Windows), DMG (macOS) | Built on their own OS runners. |

## The IPC decision, and it is forced by the offline guarantee

**The sidecar speaks JSON-RPC over stdin/stdout. Not localhost HTTP.**

This is not a style preference. `planbrain.offline.engage()` blocks every
`AF_INET` socket, and a loopback socket is still `AF_INET`. A localhost HTTP
backend would need the guard to carve out an exception for `127.0.0.1`, and an
exception is exactly the sort of thing that later grows.

stdio has no port to bind, no exception to make, and no firewall prompt on first
launch — which on Windows is a dialog most users read as *this program wants to
use the internet*, in a product whose main promise is that it does not.

**Consequence, recorded honestly:** stdio is one request at a time unless we
frame and multiplex. That is fine for a planning run and would not be for a
chatty UI. If concurrency is ever needed the answer is a second sidecar process,
not a socket.

## Non-negotiables

**`engage()` is the first statement of the packaged entry point**, before any
planning import, so an import-time call-out trips it. Enforced by a test that
reads the entry point source, because a comment saying "keep this first" is not
enforcement.

**No cross-compilation.** Separate GitHub Actions runners per OS: `windows-latest`
for MSI, `macos-latest` for DMG. Cross-compiled binaries with native extensions —
numpy, scipy, pyarrow — fail in ways that surface at a customer's machine rather
than in CI.

**Unsigned, for now.** Documented, not hidden: see `docs/install.md`. Both
platforms will warn, both warnings are alarming, and telling a user what to
expect before they see it is the difference between a cautious install and an
abandoned one.

## Size budget, committed before the first build

Bundle bloat is a silent failure: nobody notices 400 MB of unused libraries until
the download does not finish on a rural connection, which is the exact deployment
this product targets.

| artifact | budget | note |
|---|---|---|
| Python sidecar, uncompressed | **≤ 400 MB** | numpy, scipy, pandas, statsmodels and pyarrow are unavoidably large |
| Installer, compressed | **≤ 150 MB** | before any model is bundled |

Exceeding either is a finding to report and diagnose, not a number to adjust.

**These budgets exclude the LLM.** A 4B model at Q4_K_M adds roughly 2.5 GB and
is measured separately, or the model's size would hide everything else moving.

## From the reference implementation

`GGUFloader/gguf-loader` solves the `llama-cpp-python` bundling problem and two
of its answers transfer directly:

* **Skip CUDA artefacts by filename** (`cuda`, `cublas`). Bundling `ggml-cuda.dll`
  and its cuBLAS runtime adds roughly 950 MB.
* **`.dll` to `binaries`, everything else to `datas`**, and `collect_submodules()`
  for anything lazily imported, which a static analysis will miss.

**One of its answers does not transfer, and it matters.** GGUF Loader ships CPU
inference and fetches the CUDA build later via an in-app button. **That is a
runtime download, which our offline guarantee forbids outright.** Our options
are CPU-only, or shipping both variants in the installer and selecting at
startup. CPU-only until someone measures that it is too slow on the target
hardware — and 4B at Q4_K_M on CPU is the normal case for this class of machine.

Recorded now because it is the kind of constraint that gets discovered after the
GPU build is already wired in.

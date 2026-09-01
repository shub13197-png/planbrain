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

## The budget was the wrong gate, and the right one is on identity

The budget passed. The first build was 267 MB against 400 MB, comfortably
inside, and it contained **83 MB of `pyarrow`** — found only because someone read
the breakdown. **Under budget is precisely when nobody looks.**

Size was also the lesser problem. That 83 MB included `arrow_flight`, an RPC
library for moving data *between machines*, inside an application whose central
promise is that nothing leaves this one. **A size gate could not have raised
that however tight the number**, because the objection is to what the thing *is*,
not to how much it weighs.

So `packaging/bundle_manifest.py` gates on **identity**. Every top-level entry
needs an allowlist line saying what it is and why it ships; a new distribution
fails the build until someone writes one; a `FORBIDDEN` list stops a transitive
bump reinstating something already removed. Weight is reported alongside,
because the breakdown is what makes a bad entry obvious once you are looking.

**It earned its place on first run**, finding what the budget had not:

| found | why it mattered |
|---|---|
| `libssl-3` (0.8 MB) | the OpenSSL **TLS transport layer**, in an app that promises no network |
| `libcrypto-3` (5.2 MB) | kept — `_hashlib` links it for *hashing*, which is not transport. The distinction is the point |
| `charset_normalizer`, `certifi`, `urllib3` | `requests`' dependencies, pulled by a hook, never imported |
| `jinja2`, `markupsafe` | a template engine in a planning sidecar |
| `psutil` | unused |

Removing them took the bundle to **162 MB and startup from 5.4s to 2.4s**. The
6 MB was incidental; a shipped TLS stack in a product whose main claim is
offline operation was the finding.

### The gate had a hole, and closing it found more

The first version walked the filesystem tree only. PyInstaller archives
**pure-Python** packages inside the executable, where a directory walk cannot
see them — so `requests`, which is pure Python, sat in `FORBIDDEN` and **could
never have fired**. Half the gate was decorative.

It now also reads the build's `PYZ-*.toc`, counting only third-party modules;
enumerating the standard library would be hundreds of entries nobody reads.
Closing it immediately surfaced a **BeautifulSoup HTML-scraping stack** —
`bs4`, `html5lib`, `soupsieve`, `webencodings`, arriving through
`pandas.read_html` — plus IPython's `pygments` and `traitlets`. None imported at
runtime, all invisible to the previous gate.

Two further bugs surfaced while testing the fix, both the same shape as what the
gate exists to catch: the TOC search recursed from a parent directory and found
an **unrelated build's** table of contents, and an empty tree read as populated
because archived entries were merged in before the emptiness check. Both fixed,
both now tested.

## Decision: CPU-only inference. Not revisited when the model lands.

**Recorded now, before the model work starts, so it is not reopened then.**

A runtime CUDA fetch is forbidden outright by the offline guarantee — that is
what the reference implementation does and it is the one thing of its approach
that cannot transfer. So the only two options are CPU-only, or shipping both
variants in the installer.

**CPU-only.** Shipping both doubles every artifact for a 4B model doing
grammar-constrained column mapping — a task with tiny outputs, run once per
import, on a machine chosen for spreadsheets rather than tensor cores. The
GPU path would be dead weight on nearly every target machine and would blow the
installer budget on all of them.

`strip_gpu()` in the spec drops `cuda`, `cublas`, `cudnn` and `cudart`
artefacts by filename, and `ggml-cuda` plus its cuBLAS runtime is roughly
950 MB — enough to blow the committed budget in a single commit.

**What would reopen this:** a measurement showing CPU inference too slow on
target hardware for the mapping task. Not a preference, and not the availability
of a GPU build.

## Unverified surfaces

**Labelled unverified, not merely untested.** CI passing on these means the
config parses and the steps run; it does not mean anyone has watched the thing
work end to end. A reader should not mistake CI-green for verified.

| surface | status | what would verify it |
|---|---|---|
| **The Tauri compile** | **unverified.** No Rust toolchain in the environment this was written in, so `Cargo.toml`, `main.rs` and `tauri.conf.json` have never been compiled. | One successful `installer` job, then launching the MSI and DMG and clicking both buttons. |
| **The sidecar handshake through Tauri** | **unverified.** The stdio protocol is tested against the packaged binary directly; it has never been driven by the Rust shell. | The same run — the frontend showing "Offline" in its status bar is the proof. |

Everything else in this document was built and run: the sidecar starts in 2.4s,
answers requests with the guard engaged, and passes the size and identity gates
on a real build.

**These two are where the first real run will surface something.** That is the
normal shape of a first packaging pass and it is written down so nobody has to
rediscover which parts were checked.

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

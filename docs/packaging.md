# Packaging: Tauri shell, Python sidecar

**Written before the first build.** Sizes and outcomes are appended below the
line; nothing above it was revised afterwards.

## Shape

| piece | what | why |
|---|---|---|
| Shell | Tauri 2 | Native webview, no bundled Chromium. A planning grid is a table; it does not need 150 MB of browser. |
| Backend | Python, PyInstaller one-dir sidecar | The engines already exist and are the product. Rewriting `netreq` in Rust would be re-deriving verified arithmetic for no gain. |
| Store | SQLite | Single file, no service, no port, nothing for a user to install or a firewall to notice. PostgreSQL stays the option for a hosted tier. |
| Installers | MSI and NSIS (Windows), DMG (macOS, Apple Silicon and Intel) | Built on their own OS runners. NSIS because some corporate policies block MSI outright, and a planner who cannot install the thing is not a user. |

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

## The shell shipped a shape the backend does not have

**Found by asking "is this actually installable?" rather than by a failing
test**, because nothing here had ever compiled the shell and so nothing could
fail.

The backend was declared as a Tauri **`externalBin`**. That mechanism ships *one
file* and appends the host target triple to its name. Our PyInstaller build is a
**one-dir tree**: an executable plus an `_internal` directory of 889 files,
because numpy and scipy carry native libraries that must sit beside the
executable to load at all. The CI step dutifully renamed the executable and left
the tree behind.

Both halves were internally consistent, which is why this survived review. The
config was a correct `externalBin` config; the spec was a correct one-dir spec;
each was written while looking at the other's *purpose* rather than its *shape*.

**The fix is `resources`**, which ships arbitrary trees and preserves each path
relative to `tauri.conf.json`. The shell resolves
`binaries/planbrain-backend/planbrain-backend[.exe]` against
`BaseDirectory::Resource`.

That path is now written down in three places — the Rust constant, the config
glob, and the workflow's staging step — and
`tests/test_desktop_shell.py` asserts all three agree. Three copies of a string
is not ideal; three copies that can drift silently and fail at *launch* rather
than at build is worse. The rule is asserted rather than the copies removed,
because the config cannot read the Rust and the workflow cannot read either.

### Spawned by `std::process`, not the shell plugin

The obvious way to start a sidecar in Tauri is `tauri-plugin-shell`. We do not
use it. We are not running a user-supplied command; we are running one binary we
shipped, at a path we computed. The plugin would add a scope to configure, a
permission to grant, and a dependency to audit, and would buy nothing.

The consequence is that `capabilities/default.json` grants exactly two things:
`core:default` for a window, and `dialog:allow-open` for choosing a spreadsheet.
Not `dialog:allow-save`, not `fs:`, not `shell:`. The set is asserted as an exact
set, so widening it is a decision someone has to make on purpose.

### The icon set is drawn in code

There was no icon set at all — the bundler would have failed on its first run.
`packaging/make_icons.py` generates all sixteen files, both containers included,
from one drawing. The ICNS container is written by hand because `iconutil` is
macOS-only and the icons have to be reproducible on any runner.

Generated rather than committed as binaries so a reviewer can see what the mark
is without opening an image editor, and so it can be re-rendered at any size. The
release generates them before it bundles, and a test asserts that order: a build
that skipped the generator would bundle whatever happened to be stale in the
checkout, which is the failure that looks like success.

## Getting it to a user

An installer nobody can find is not a distribution. `.github/workflows/release.yml`
runs on a version tag, builds all three targets on their own runners, and
attaches them to a **draft** GitHub Release with `SHA256SUMS.txt`.

Draft, because a human should look before it is public.

**The checksums exist because the install guide already told people to compare
one.** It had said so for weeks and nothing produced the file — instructions
pointing at an artifact that does not exist, which is worse than no instructions,
because a user who follows them concludes the download is wrong. A test now ties
the two together: the release must generate `SHA256SUMS.txt` and the guide must
name it.

The checksum is not a signature and the release notes say so. Anyone able to
replace the installer could replace the checksum file beside it. It catches a
truncated download or a bad mirror, which is the realistic failure, and it is
what we have until a certificate exists.

The release runs `check_size.py`, `bundle_manifest.py` and `check_offline_run.py`
before it bundles anything, and a test asserts it does. The shipped artifact must
not be the least-verified build in the project, which is what it becomes the
moment the installer path gets its own shortcut.

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
| **The resource path** | **unverified.** That a `resources` glob installs to a path preserved relative to `tauri.conf.json` is read from Tauri's documentation, not observed. The three copies of the path are asserted to agree with *each other*; nothing here proves they agree with the bundler. | An installed build launching. If the assumption is wrong the symptom is specific and the error message names the path it looked for. |
| **The installer on a real machine** | **unverified.** No MSI, NSIS bundle or DMG has been produced. The SmartScreen and Gatekeeper text in `docs/install.md` is from the platform documentation, not from watching the dialogs appear. | Downloading a release artifact and installing it on a machine that has never seen the source. |

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

## The Linux artifact is not statically linked, and now we know

The first Linux bundle that ever got past the identity gate could not start:

    error while loading shared libraries: libz.so.1

It was running in `gcr.io/distroless/base-debian12`, a container with almost
nothing in it. PyInstaller's bootloader is an ordinary ELF executable and its
own `DT_NEEDED` entries resolve from the system at exec time, before anything in
`_internal` is reachable. So the artifact needs a handful of ordinary system
libraries, and always did -- on Windows and macOS too, where the equivalents
ship with the OS and nobody notices.

**The container was testing a claim nobody had made.** The promise is *no
network*, and `--network=none` is what tests it. The base image is now
`debian:12-slim`, which resembles a machine somebody might actually own; a base
image resembling nothing tests the wrong thing convincingly.

**Written into `docs/install.md` rather than left in a workflow comment**,
because it is a thing a Linux user can hit: on a minimal or server install they
may need `zlib1g` and `libwebkit2gtk-4.1-0`. "Self-contained" remains true in
the sense that matters -- no runtime to install, no packages to fetch, no
network -- and "statically linked" was never true and is not claimed.

## The allowlist was checked on one machine, and one machine is not the matrix

Two rounds of this, on consecutive runs:

* **Round one** was spelling. Twenty entries rejected on Linux and macOS because
  every allowlist key carried Windows naming. Fixed with `canonical()`.
* **Round two** was *contents*. The Windows CI build carries `ucrtbase` and
  thirteen `api-ms-win-*` API-set forwarders. **A local Windows build on Python
  3.14 does not.** Same operating system, same spec, different interpreter
  build, fourteen different files.

That second one is the more useful lesson, because the first is the kind of
mistake you can imagine avoiding and the second is not. The local build that
passed both gates at 159.9 MB was a real check and it was not the same check CI
runs. The thirteen forwarders collapse to one allowlist entry -- thirteen lines
saying the same sentence is thirteen lines nobody reads.


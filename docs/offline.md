# Offline operation, audited

**Requirement:** the packaged app runs fully offline. No data leaves the machine,
ever. This is an **audited property**, not a claim — three independent checks,
one of which cannot be satisfied by writing code carefully.

| check | what it proves | where |
|---|---|---|
| Dependency audit | what each dependency *can* reach, and when | this page |
| In-process socket block | a leak fails loudly on a customer's laptop | `planbrain/offline.py` |
| CI with `--network=none` | the pipeline needs nothing from the network | `.github/workflows/ci.yml` |

The container check is the strong one. With no network interface present, a leak
cannot succeed regardless of what any dependency attempts — it does not depend on
having audited correctly, or on the audit staying true as versions move.

## Result

**The full pipeline and all 534 tests run with no network interface.** Verified
by `docker run --network=none`, not by inspection.

The in-process guard is verified separately **with a network available**, so
that a passing offline run cannot be the container quietly covering for a guard
that never engaged.

## The runtime dependency closure

Two declared dependencies pull in 29 distributions. Scanned for network imports
(`urllib`, `requests`, `httpx`, `socket`, `aiohttp`, `http`):

| package | network-capable code | reachable from our use? |
|---|---|---|
| `jsonschema` | `requests` in `validators.py`, `urllib` in 4 files | **No** — see below |
| `referencing` | `urllib` in `_core.py` | No — URI *parsing*, not fetching |
| `fsspec` | `aiohttp`, `requests`, `urllib` across 14 files | **No** — only for URL-shaped paths |
| `fugue` | `requests` in `rpc/flask.py` | No — distributed RPC, never constructed |
| `pyarrow` | `requests`, `urllib`, `socket` | No — mostly test files; S3/GCS filesystems unused |
| `tqdm` | `requests` in `contrib/discord.py`, `contrib/telegram.py` | No — optional contrib, never imported |
| everything else | none found | — |

**No telemetry, no version checks, no model downloads, no phone-home found in
any of the 29.** The statsforecast models used here (AutoETS, CrostonOptimized,
TSB) are fitted locally from the data given; nothing is downloaded.

### The two conditional paths, checked rather than assumed

**`jsonschema` remote `$ref`.** Its closure contains `requests` because the
deprecated `RefResolver` fetched remote schema references. Two independent
reasons this cannot fire here, both asserted by tests:

1. **The library refuses.** Modern `jsonschema` resolves references through
   `referencing`, which raises `Unresolvable` for a remote URI rather than
   fetching it. Verified empirically, because it is a library default a future
   version could change.
2. **Our schema has none.** Every `$ref` in `solver_io.schema.json` is local
   (`#/$defs/...`), asserted by `test_our_own_contract_has_no_remote_references`
   and by the older `test_contract_is_self_contained`.

The `$id` field is `https://planbrain.dev/schema/solver_io.json`. That is an
identifier, not a location, and nothing dereferences it.

**`fsspec` HTTP filesystem.** Arrives via `triad` ← `fugue` ← `statsforecast`.
It dispatches on path scheme, so `http://` or `s3://` would reach out. Every
path this project passes is a local file or an in-memory SQLite connection.
There is no code path where a user-supplied string becomes an fsspec URL — the
importer takes a directory and reads it with `csv` and `openpyxl` directly.

## What the in-process guard does and does not cover

`planbrain.offline.engage()` replaces `socket.socket` and
`socket.create_connection` so any non-local address family raises
`NetworkAccessDenied` at the point of attempt.

**Covers:** every pure-Python client. `requests`, `urllib`, `httpx`, `aiohttp`
and anything else in the closure all bottom out in `socket`.

**Does not cover**, and these are named rather than papered over:

* **A C extension opening a file descriptor itself.** `pyarrow` and `scipy`
  contain native code that could in principle bypass the Python socket module.
* **A subprocess.** Nothing here spawns one, but the guard would not stop it.
* **A pre-existing connection.** The guard blocks new sockets, not open ones.
  Nothing opens any before `engage()`, which is called first thing at startup.

Each of those is exactly what the `--network=none` container check covers, which
is why the guarantee rests on both and not either alone.

## The packaged artifact is checked, not just the checkout

The first version of this audit ran in a checkout, and the packaged environment
turned out to differ — the test suite could not even import `tools` there. So
`.github/workflows/package.yml` builds the sidecar, puts it in a container with
**no network interface**, and runs it:

    docker run --rm -i --network=none planbrain-sidecar /app/planbrain-backend/...

`packaging/check_offline_run.py` then asserts the handshake reported
`offline: true` and every request succeeded — because the process exits 0 whether
or not the guard engaged and whether or not every request failed.

**Stated limit:** `--network=none` needs a container, so this runs on Linux.
GitHub's Windows and macOS runners cannot remove the interface from a native
process, so those platforms assert the in-process guard from the packaged
binary's handshake instead. That is weaker, and it is labelled weaker rather
than described as the same check.

## Where the guard is engaged

`tools/demo.py` calls `engage()` before importing any planning module, so
anything reaching the network *at import time* — a version check on first import
is a real pattern — trips it too.

**When desktop packaging lands, its entry point must call `engage()` as its
first statement.** That is the single wiring requirement for the packaged app,
and it is recorded here because it is the sort of thing that gets discovered
after shipping.

## Outside the app boundary

Anything the user runs deliberately to fetch public data is **not the
application reaching the network**. A one-time download script, run by the user
before the app is started, is a different thing from an app that calls out while
running, and the distinction should be kept in the packaging: such a script
belongs outside the app bundle, documented as a manual prerequisite.

**There is currently no such script in this repository.** If one is added, it
goes in a clearly separate location, is never invoked by application code, and
is excluded from the offline test boundary by construction rather than by
convention.

## Constraint on future dependencies

Any dependency added from here must run without network access at runtime. The
one already on the horizon:

**Road distances (`pyvroom` + OSRM).** `haulplan` currently takes distances from
its caller and `docs/haulplan.md` defers real road routing. **The
offline-compatible answer is a self-hosted OSRM with a local OSM extract, not a
hosted routing API.** A hosted API would send customer origin-destination pairs
off the machine, which is precisely what this requirement forbids — and it would
leak the customer's depot network and delivery patterns, which is commercially
sensitive quite apart from the guarantee.

Recorded as a **constraint on entry** rather than a discovery to be made after
the dependency is added: self-hosted OSRM means an extra service and a multi-GB
regional extract in the deployment, and that cost belongs in the decision to
adopt it, not after.

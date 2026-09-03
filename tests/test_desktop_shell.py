"""The desktop shell and the installer pipeline, checked without compiling them.

There is no Rust toolchain in this environment, so nothing here proves the shell
builds — `docs/packaging.md` says so plainly and CI is the only thing that can
settle it. What these tests do cover is the class of defect that a successful
compile would *not* catch: two files that each look right and disagree with each
other.

That is not hypothetical. The shell originally declared the backend as a Tauri
`externalBin`, which ships one file, while the build produces a one-dir tree of
890. Both halves were internally consistent. The mismatch would have surfaced as
a failed bundle in CI at best, and as an installed application that launches to a
missing backend at worst.

So the rules asserted here are all cross-file: the path the Rust resolves against
the glob the config installs, the plugins the frontend calls against the crates
that provide them, the platforms the release builds against the platforms the
install guide names.
"""

import ast
import json
import re
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "desktop" / "src-tauri"
CONF = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
MAIN_RS = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
CARGO = (TAURI / "Cargo.toml").read_text(encoding="utf-8")
#: Comments explain why a crate was *dropped*, which reads as a dependency to a
#: substring search. The first version of this file asserted against the raw
#: text and failed on its own explanation.
CARGO_CODE = "\n".join(line.split("#", 1)[0] for line in CARGO.splitlines())
INDEX = (ROOT / "desktop" / "dist" / "index.html").read_text(encoding="utf-8")
RELEASE = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the backend, and where it lands
# --------------------------------------------------------------------------

def _backend_dir_from_rust() -> str:
    """The directory main.rs resolves the backend from."""
    match = re.search(r'const BACKEND_DIR: &str = "([^"]+)"', MAIN_RS)
    assert match, "main.rs no longer declares BACKEND_DIR; this test cannot check it"
    return match.group(1)


def test_the_backend_ships_as_a_resource_tree_not_an_external_binary():
    """`externalBin` ships a single file. PyInstaller gives us a directory —
    numpy and scipy carry native libraries that must sit beside the executable.
    This was the actual defect; it is asserted so it cannot come back as a
    plausible-looking simplification."""
    bundle = CONF["bundle"]
    assert "externalBin" not in bundle, (
        "externalBin cannot carry the _internal tree the backend needs"
    )
    assert bundle.get("resources"), "nothing ships the backend into the bundle"


def test_the_path_the_shell_resolves_is_inside_the_glob_the_bundle_installs():
    """The one invariant that spans Rust and JSON.

    A `resources` entry keeps its path relative to tauri.conf.json, so the
    directory in the glob is the directory the Rust must ask for. Drop the
    `binaries/` prefix on either side and the app compiles, installs, and fails
    at launch — the worst place to find out.
    """
    backend_dir = _backend_dir_from_rust()
    prefixes = [g.split("*")[0].rstrip("/") for g in CONF["bundle"]["resources"]]
    assert backend_dir in prefixes, (
        f"main.rs resolves {backend_dir!r}, but the bundle installs {prefixes}"
    )


def test_the_release_stages_the_backend_where_the_bundle_looks_for_it():
    """The third copy of the same path, in the workflow that assembles it."""
    backend_dir = _backend_dir_from_rust()
    staged = f"desktop/src-tauri/{backend_dir}"
    steps = RELEASE["jobs"]["build"]["steps"]
    scripts = "\n".join(s.get("run", "") for s in steps)
    assert staged in scripts, (
        f"the release workflow never copies the backend to {staged}"
    )


# --------------------------------------------------------------------------
# icons
# --------------------------------------------------------------------------

def _declared_icon_names() -> set[str]:
    """What packaging/make_icons.py claims it writes.

    Read out of the source with `ast` rather than by importing it, so this test
    does not need Pillow — a build-time dependency that has no business being
    required to run the suite.
    """
    tree = ast.parse((ROOT / "packaging" / "make_icons.py").read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "PNG_SIZES":
            names |= set(ast.literal_eval(node.value))
    assert names, "PNG_SIZES could not be read out of make_icons.py"
    return names | {"icon.ico", "icon.icns"}


def test_every_icon_the_generator_declares_is_present_and_not_empty():
    """A half-written icon directory is the empty-result-reads-as-success shape:
    the bundler takes what it finds and the shortfall shows up as a blank tile."""
    icons = TAURI / "icons"
    missing = [n for n in _declared_icon_names() if not (icons / n).exists()]
    assert not missing, f"make_icons.py declares these but they are not on disk: {missing}"
    empty = [n for n in _declared_icon_names() if (icons / n).stat().st_size == 0]
    assert not empty, f"zero-byte icons: {empty}"


def test_every_icon_the_config_names_exists():
    """The bundler resolves these paths itself and fails late if one is wrong."""
    missing = [p for p in CONF["bundle"]["icon"] if not (TAURI / p).exists()]
    assert not missing, f"tauri.conf.json names icons that do not exist: {missing}"


def test_the_release_generates_the_icons_before_it_bundles():
    """The icon set is generated, not committed as opaque binaries — so a build
    that skips the generator bundles whatever is stale in the checkout."""
    steps = RELEASE["jobs"]["build"]["steps"]
    order = [s.get("run", "") for s in steps]
    generate = next(i for i, r in enumerate(order) if "make_icons.py" in r)
    build = next(i for i, r in enumerate(order) if "tauri build" in r)
    assert generate < build, "the icons are generated after the bundle is built"


# --------------------------------------------------------------------------
# what the shell is allowed to do
# --------------------------------------------------------------------------

CAPABILITY = json.loads(
    (TAURI / "capabilities" / "default.json").read_text(encoding="utf-8")
)

#: Everything this application may do, and why. Widening this set is a decision;
#: the test exists so it has to be made deliberately rather than by a copied
#: snippet from a tutorial.
GRANTED = {
    "core:default": "a window, and the invoke bridge to our one command",
    "dialog:allow-open": "choosing a spreadsheet to import",
}


def test_the_capability_grants_nothing_beyond_the_allowlist():
    """Least privilege, asserted as an exact set rather than a count, so adding
    a permission fails here and removing one does not silently pass."""
    granted = set(CAPABILITY["permissions"])
    assert granted == set(GRANTED), (
        f"unexplained: {sorted(granted - set(GRANTED))}; "
        f"gone: {sorted(set(GRANTED) - granted)}"
    )


def test_no_shell_execution_permission_is_granted():
    """The backend is spawned by std::process at a path we computed. A shell
    scope would be a permission to grant and an attack surface to reason about
    for no benefit — and the config would still work, which is why this is
    checked rather than assumed."""
    assert not any(p.startswith("shell:") for p in CAPABILITY["permissions"])
    assert "tauri-plugin-shell" not in CARGO_CODE
    assert "tauri_plugin_shell" not in MAIN_RS


def test_nothing_is_permitted_to_write_outside_the_application_directory():
    """`dialog:allow-save` and the filesystem plugin are both absent. The import
    flow reads a file the user chose; it never writes one back."""
    for permission in CAPABILITY["permissions"]:
        assert not permission.startswith("fs:"), permission
        assert permission != "dialog:allow-save"


def test_the_capability_applies_to_a_window_that_exists():
    """A capability scoped to a window label that no window has is silently inert
    — the permissions simply never apply, and the failure looks like a bug in
    the frontend."""
    labels = {w["label"] for w in CONF["app"]["windows"]}
    assert set(CAPABILITY["windows"]) <= labels, (
        f"capability targets {CAPABILITY['windows']}, but the windows are {labels}"
    )


# --------------------------------------------------------------------------
# frontend against backend
# --------------------------------------------------------------------------

def test_the_frontend_invokes_only_commands_the_shell_exposes():
    """`invoke` on an unregistered command fails at runtime with a message the
    user sees and cannot act on."""
    called = set(re.findall(r'invoke\(\s*"([^"]+)"', INDEX))
    handler = re.search(r"generate_handler!\[([^\]]*)\]", MAIN_RS)
    assert handler, "main.rs registers no commands"
    registered = {name.strip() for name in handler.group(1).split(",") if name.strip()}
    assert called <= registered, f"not registered in Rust: {sorted(called - registered)}"


#: The JS namespace a plugin exposes, and the crate that has to be present and
#: initialised for it to exist at runtime.
PLUGIN_BRIDGES = {"dialog": ("tauri-plugin-dialog", "tauri_plugin_dialog::init()")}


def test_every_plugin_the_frontend_reaches_for_is_a_dependency_and_initialised():
    """`window.__TAURI__.dialog` is `undefined` if the crate is missing or never
    initialised, and the file picker fails with a TypeError rather than anything
    that names the cause."""
    used = set(re.findall(r"window\.__TAURI__\.(\w+)", INDEX)) - {"core", "event"}
    for namespace in used:
        assert namespace in PLUGIN_BRIDGES, (
            f"the frontend uses window.__TAURI__.{namespace} and this test does "
            f"not know which crate provides it"
        )
        crate, init = PLUGIN_BRIDGES[namespace]
        assert crate in CARGO_CODE, f"{namespace} is used but {crate} is not a dependency"
        assert init in MAIN_RS, f"{crate} is a dependency but {init} is never called"


def test_the_content_security_policy_admits_no_remote_origin():
    """The offline guarantee has to hold in the webview too. A stylesheet pulled
    from a CDN is data leaving the machine, and the backend's socket guard cannot
    see it — the webview is a separate process."""
    csp = CONF["app"]["security"]["csp"]
    assert "//" not in csp, f"the CSP names a remote origin: {csp}"
    assert "*" not in csp, f"the CSP contains a wildcard source: {csp}"


# --------------------------------------------------------------------------
# the release itself
# --------------------------------------------------------------------------

def test_the_release_runs_every_gate_the_backend_has():
    """The installer path must not be a way around the checks. Each of these
    caught something real; skipping them here would make the shipped artifact
    the least-verified build in the project."""
    steps = RELEASE["jobs"]["build"]["steps"]
    scripts = "\n".join(s.get("run", "") for s in steps)
    for gate in ("check_size.py", "bundle_manifest.py", "check_offline_run.py"):
        assert gate in scripts, f"the release never runs {gate}"


def test_every_script_the_release_runs_exists():
    """A renamed script turns into a red workflow on tag day, which is the worst
    possible day to find it."""
    scripts = "\n".join(
        s.get("run", "") for s in RELEASE["jobs"]["build"]["steps"]
    )
    for path in set(re.findall(r"(packaging/[\w./-]+\.py)", scripts)):
        assert (ROOT / path).exists(), f"the release runs {path}, which does not exist"


def test_the_release_publishes_checksums():
    """docs/install.md tells the user to compare a checksum. Something has to
    produce one — the guide said so for weeks while nothing did."""
    scripts = "\n".join(
        s.get("run", "") for job in RELEASE["jobs"].values() for s in job["steps"]
    )
    assert "SHA256SUMS.txt" in scripts
    assert "sha256sum" in scripts or "shasum" in scripts
    guide = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" in guide, (
        "the release publishes a checksum file the install guide never names"
    )


def test_the_install_guide_names_every_platform_the_release_builds():
    """The guide claimed Intel Macs were not built for. Adding the runner without
    fixing the sentence would leave a published statement that is simply false,
    and nothing else would have noticed."""
    guide = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    triples = {e["triple"] for e in RELEASE["jobs"]["build"]["strategy"]["matrix"]["include"]}
    words = {
        "x86_64-pc-windows-msvc": "Windows",
        "aarch64-apple-darwin": "Apple Silicon",
        "x86_64-apple-darwin": "Intel",
        "x86_64-unknown-linux-gnu": "AppImage",
    }
    for triple in triples:
        assert triple in words, f"no install-guide wording is defined for {triple}"
        assert words[triple] in guide, (
            f"the release builds {triple} but the install guide never mentions "
            f"{words[triple]!r}"
        )


def test_no_cross_compilation():
    """Native extensions cross-compiled fail on a customer's machine rather than
    in CI. One runner per target, and the pairing is asserted."""
    host = {
        "windows-latest": "pc-windows",
        "macos-latest": "apple-darwin",
        "macos-15-intel": "apple-darwin",
        "ubuntu-22.04": "linux-gnu",
    }
    for entry in RELEASE["jobs"]["build"]["strategy"]["matrix"]["include"]:
        assert host[entry["os"]] in entry["triple"], (
            f"{entry['os']} is building {entry['triple']}"
        )


def test_the_release_fails_rather_than_publishing_an_empty_release():
    """An artifact step with nothing to upload is the empty-result-reads-as-
    success shape at its most expensive: a published release containing no
    installers, announced to whoever is watching the repository."""
    steps = RELEASE["jobs"]["build"]["steps"]
    upload = [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
    assert upload, "the release uploads nothing"
    assert all(s["with"].get("if-no-files-found") == "error" for s in upload)
    scripts = "\n".join(s.get("run", "") for s in steps)
    assert "exit 1" in scripts, "nothing fails the job when no installer is produced"


def test_the_frontend_calls_only_backend_methods_that_exist():
    """`call("plan.run")` on a method the backend does not register fails at
    runtime, in front of the user, with a message about an unknown method.

    Nothing else checks this: the Rust never inspects the string, the backend
    never sees the frontend, and there is no browser test. The two halves are
    joined by a quoted name and by nothing else.
    """
    from planbrain.backend.api import METHODS

    called = set(re.findall(r'call\(\s*"([^"]+)"', INDEX))
    assert called, "no backend calls found; this test is not looking where it thinks"
    unknown = sorted(called - set(METHODS))
    assert unknown == [], f"the frontend calls methods the backend does not have: {unknown}"


def test_the_planning_pane_reaches_the_planner():
    """The window had the import flow and nothing else -- every plan ran through
    a terminal, which made the audience "a manufacturer comfortable with a
    command line". These are the calls that close that gap; if the pane is
    removed this fails rather than the app quietly reverting to an importer."""
    called = set(re.findall(r'call\(\s*"([^"]+)"', INDEX))
    for method in ("demo.build", "scenario.growth", "plan.run"):
        assert method in called, f"the interface never calls {method}"


def test_progress_lines_are_not_mistaken_for_a_response():
    """A plan run takes the better part of a minute and reports its stage as it
    goes. Those lines carry a request id, so a client that looked up the waiter
    first would resolve the promise on the first one and hand the caller a
    half-finished plan."""
    progress_at = INDEX.index("if (msg.progress)")
    waiter_at = INDEX.index("const waiter = pending.get(msg.id)")
    assert progress_at < waiter_at, (
        "the progress check must come before the waiter lookup"
    )


"""Where the planning database lives, and that it lives at all.

**The packaged application ran entirely in memory.** `main()` called `session()`
with its default, which is `":memory:"`, so closing the window discarded
everything: the imported history, the column mapping, the plan. Nothing was
saved and nothing said so.

`docs/install.md` meanwhile told the user, in three separate places, that their
data is in a file:

    * **One application and one SQLite file.**
    * **Your data stays in a file you can see.** Delete it and it is gone;
      copy it and you have a backup.
    * [uninstalling] Neither removes your planning database ... It lives at:
      Windows -- %LOCALAPPDATA%\\PlanningBrain\\

That directory was never created by anything. A promise nobody had checked, in
the one document a user reads before trusting the application with a year of
their history.

The tests here are the two halves of it: the file is real and survives a
restart, and the path is the path the guide names.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from planbrain.backend import api
from planbrain.demo import build_demo
from planbrain.facts.access import read_facts
from planbrain.forecast import demand_keys

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
ENTRY = (ROOT / "planbrain" / "backend" / "__main__.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# it survives a restart
# --------------------------------------------------------------------------

def test_a_second_session_reads_what_the_first_one_wrote(tmp_path):
    """The whole point. Closing the window must not discard the work."""
    path = tmp_path / "planning.db"

    first = api.session(str(path))
    # A direct api call, where the caller owns the transaction. The
    # application's own boundary is one request, and it is asserted against the
    # real process in the next test rather than here.
    with first.con:
        api.demo_build(first, seed=7)
    demo = first.demo
    first.con.close()

    assert path.exists(), "no database file was created"

    # Through the accessor, on a connection that has never seen this data: this
    # is a read of a file, not of a cache.
    second = api.session(str(path))
    facts = read_facts(
        second.con, "fact_supply_demand", scenario_id=0, measure="demand_actual",
        start=demo.history_start, end=demo.history_end,
        # The keys that actually carry independent demand. Asking for the first
        # five parts instead returns a legitimate run of zeros -- they are raw
        # materials, whose demand is dependent and arrives through the BOM.
        keys=demand_keys(demo)[:5],
    )
    second.con.close()

    assert len(demo.parts) == 200
    assert sum(1 for f in facts if f.qty) > 0, (
        "the file exists but the facts did not survive the restart"
    )


def test_opening_an_existing_database_does_not_reapply_the_schema(tmp_path):
    """`schema.sql` creates tables and seeds scenario 0.

    Running it a second time raises `table scenario already exists`, so an
    application that opened its own file on the second launch would crash before
    it printed a line. Reapplying it *successfully* would be worse: the seed row
    would double and every read keyed on scenario 0 would find two.
    """
    path = tmp_path / "planning.db"
    api.session(str(path)).con.close()

    reopened = api.session(str(path))
    scenarios = reopened.con.execute(
        "SELECT count(*) FROM scenario WHERE scenario_id = 0"
    ).fetchone()[0]
    reopened.con.close()

    assert scenarios == 1


def test_an_in_memory_session_still_gets_a_schema():
    """Every test in this repo depends on it, and so does `--db :memory:`."""
    state = api.session(":memory:")
    assert state.con.execute(
        "SELECT count(*) FROM scenario"
    ).fetchone()[0] == 1


# --------------------------------------------------------------------------
# it survives the window closing, which is not a graceful shutdown
# --------------------------------------------------------------------------

def _drive(path, requests):
    """Speak the real protocol to the real entry point, then kill it.

    `subprocess.run` cannot express this: it closes stdin, the loop ends of its
    own accord, and the process exits normally. The failure only appears when
    the process is *killed* mid-session, which is what the shell does on
    `WindowEvent::Destroyed` -- so the pipe stays open and the process dies
    under it.

    Returns the handshake and one reply per request, with progress lines
    skipped: they carry the same id and are distinguished by `progress`, so a
    reader that ignores them still sees exactly one response per request.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "planbrain.backend", "--db", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT,
    )
    try:
        handshake = json.loads(proc.stdout.readline())
        replies = []
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                assert line, "the backend died without answering"
                reply = json.loads(line)
                if not reply.get("progress"):
                    break
            replies.append(reply)
        return handshake, replies
    finally:
        # No `shutdown` request, deliberately. This is the window closing.
        proc.kill()
        proc.wait(timeout=30)


def test_work_the_backend_answered_ok_for_survives_being_killed(tmp_path):
    """`ok: true` has to mean the write reached the disk.

    The shell kills the sidecar on `WindowEvent::Destroyed` without sending
    `shutdown`, so nothing gets a chance to commit on the way out. Everything
    the backend has already answered `ok` for must survive that.

    Why it did not, and what was rejected instead, is in `docs/decisions.md`
    under *A request is the transaction boundary*.
    """
    path = tmp_path / "planning.db"
    handshake, replies = _drive(
        path, [{"id": 1, "method": "demo.build", "params": {"seed": 7}}]
    )

    assert handshake["ready"] is True
    assert replies[0]["ok"] is True, "the demo build itself failed"

    assert path.exists(), "no database file was created"

    # Through the accessor, like every other read in the repo. The keys and the
    # bucket range come from a second, database-free build on the same seed:
    # the generator is deterministic, so these are the keys the killed process
    # wrote under, and asking for them costs a third of a second rather than a
    # raw scan of a fact table.
    expected = build_demo(seed=7)
    reopened = api.session(str(path))
    facts = read_facts(
        reopened.con, "fact_supply_demand", scenario_id=0, measure="demand_actual",
        start=expected.history_start, end=expected.history_end,
        keys=demand_keys(expected)[:5],
    )
    reopened.con.close()

    assert sum(1 for f in facts if f.qty) > 0, (
        "the backend answered ok for demo.build and the file has no demand in "
        "it: the transaction was never committed, so the kill rolled it back"
    )


# --------------------------------------------------------------------------
# the path is the one the guide documents
# --------------------------------------------------------------------------

def test_the_database_lands_where_the_install_guide_says_it_does(monkeypatch):
    """Cross-file: the code's path against the prose the user reads.

    The uninstall section tells people to delete this directory to remove their
    data. If the two ever disagree, the instruction deletes the wrong folder and
    leaves the real one behind -- and the user believes their data is gone when
    it is not.
    """
    documented = {
        "win32": r"%LOCALAPPDATA%\PlanningBrain\\",
        "darwin": "~/Library/Application Support/PlanningBrain/",
        "linux": "~/.local/share/PlanningBrain/",
    }
    for platform, text in documented.items():
        assert text.rstrip("\\") in INSTALL or text in INSTALL, (
            f"docs/install.md no longer documents the {platform} path {text!r}"
        )

    # A POSIX-shaped value on purpose, even though this is the Windows branch.
    #
    # `pathlib.Path` is the *running* platform's flavour, so on Linux a
    # backslash is an ordinary character rather than a separator: the first
    # version of this passed a Windows-style LOCALAPPDATA, which splits into
    # four components on Windows and stays one on Linux. It passed on the
    # machine it was written on and failed in CI, which is the only place
    # this suite meets Linux.
    #
    # What is under test is that the win32 branch reads LOCALAPPDATA and
    # lands PlanningBrain/planning.db beneath whatever it holds -- not how
    # a string is split into components.
    monkeypatch.setenv("LOCALAPPDATA", "/appdata/local")
    monkeypatch.setattr(sys, "platform", "win32")
    assert api.default_database() == Path("/appdata/local/PlanningBrain/planning.db")


@pytest.mark.parametrize("platform, tail", [
    ("darwin", "Library/Application Support/PlanningBrain"),
    ("linux", ".local/share/PlanningBrain"),
])
def test_each_platform_uses_its_own_convention(monkeypatch, platform, tail):
    """A single hardcoded path would work on the machine it was written on."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert api.default_database().parent.as_posix().endswith(tail)


# --------------------------------------------------------------------------
# the regression guard
# --------------------------------------------------------------------------

def test_the_packaged_entry_point_does_not_run_in_memory():
    """This is the bug, asserted where it happened.

    `session()` with no argument is `":memory:"`, which is right for a test and
    silently wrong for the application: it discards a year of imported history
    when the window closes, and the install guide promises the opposite.
    """
    assert "session()" not in ENTRY, (
        "the backend entry point opens a session with no path, which is an "
        "in-memory database -- nothing the user does will be saved"
    )
    assert "default_database" in ENTRY

"""CI gate: nothing outside the accessor may read a fact table directly.

The sparse-storage rule cannot be enforced by the database, so it is enforced
here instead. Docs do not prevent an inner join; a failing build does.
"""

from pathlib import Path

from tools.check_fact_access import ALLOWED, EXEMPT_PACKAGE_PREFIX, scan

ROOT = Path(__file__).resolve().parents[1]


def test_repo_has_no_unauthorised_fact_reads():
    assert scan(ROOT) == []


def test_the_gate_actually_catches_a_direct_read(tmp_path):
    (tmp_path / "rogue.py").write_text(
        'q = "SELECT qty FROM fact_supply_demand WHERE sku_id = 1"\n'
    )
    violations = scan(tmp_path)
    assert len(violations) == 1
    assert "rogue.py" in violations[0]


def test_the_gate_catches_a_join_too(tmp_path):
    (tmp_path / "rogue.py").write_text(
        'q = "SELECT * FROM part JOIN fact_capacity USING (bucket_date)"\n'
    )
    assert len(scan(tmp_path)) == 1


def test_writes_are_not_blocked(tmp_path):
    """Absent-means-zero is a read hazard. Importers must still be able to INSERT."""
    (tmp_path / "writer.py").write_text(
        'q = "INSERT INTO fact_supply_demand (sku_id) VALUES (1)"\n'
    )
    assert scan(tmp_path) == []


def test_no_planning_module_is_exempt():
    """The invariant that matters: nothing that computes a plan number is exempt.

    The allowlist may grow with storage-layer tests, but the moment netreq or
    rccp appears on it, the chokepoint has stopped meaning anything.
    """
    planning = [
        p for p in ALLOWED
        if p.startswith("planbrain/") and not p.startswith(EXEMPT_PACKAGE_PREFIX)
    ]
    assert planning == []


def test_allowlist_has_no_stale_entries():
    """A path that no longer exists is an exemption nobody is reviewing."""
    assert [p for p in ALLOWED if not (ROOT / p).exists()] == []

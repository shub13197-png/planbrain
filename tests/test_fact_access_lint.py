"""CI gate: nothing outside the facts package may touch a fact table directly.

Reads and writes are both hazards, and neither is enforced by the database:
a raw SELECT drops zero buckets, a raw INSERT skips sparsification and routes
around the frozen-scenario check that makes a committed snapshot immutable.

Docs do not prevent either. A failing build does.
"""

from pathlib import Path

from planbrain.facts.grains import FACT_TABLES
from tools.check_fact_access import ALLOWED, EXEMPT_PACKAGE_PREFIX, scan

ROOT = Path(__file__).resolve().parents[1]


def test_repo_has_no_unauthorised_fact_access():
    assert scan(ROOT) == []


def test_the_gate_catches_a_direct_read(tmp_path):
    (tmp_path / "rogue.py").write_text(
        'q = "SELECT qty FROM fact_supply_demand WHERE sku_id = 1"\n'
    )
    violations = scan(tmp_path)
    assert len(violations) == 1
    assert "direct read" in violations[0]
    assert "read_facts" in violations[0]


def test_the_gate_catches_a_join(tmp_path):
    (tmp_path / "rogue.py").write_text(
        'q = "SELECT * FROM part JOIN fact_capacity USING (bucket_date)"\n'
    )
    assert len(scan(tmp_path)) == 1


def test_the_gate_catches_an_insert(tmp_path):
    (tmp_path / "rogue.py").write_text(
        'q = "INSERT INTO fact_supply_demand (sku_id) VALUES (1)"\n'
    )
    violations = scan(tmp_path)
    assert len(violations) == 1
    assert "direct write" in violations[0]
    assert "write_facts" in violations[0]


def test_the_gate_catches_an_update(tmp_path):
    (tmp_path / "rogue.py").write_text('q = "UPDATE fact_capacity SET qty = 0"\n')
    violations = scan(tmp_path)
    assert len(violations) == 1
    assert "direct write" in violations[0]


def test_the_gate_catches_a_delete(tmp_path):
    """DELETE FROM also matches the read pattern; it must be named as a write."""
    (tmp_path / "rogue.py").write_text('q = "DELETE FROM fact_fleet WHERE truck_id = 1"\n')
    violations = scan(tmp_path)
    assert len(violations) == 1
    assert "direct write" in violations[0]


def test_non_fact_tables_are_not_gated(tmp_path):
    """The scenario and measure tables are ordinary; only fact grains are hazardous."""
    (tmp_path / "fine.py").write_text(
        'q = "SELECT name FROM scenario JOIN measure ON 1=1"\n'
    )
    assert scan(tmp_path) == []


def test_gate_covers_every_registered_grain(tmp_path):
    """Derived from grains.py, so a table added later is covered without edits here."""
    for i, table in enumerate(FACT_TABLES):
        (tmp_path / f"rogue{i}.py").write_text(f'q = "SELECT qty FROM {table}"\n')
    assert len(scan(tmp_path)) == len(FACT_TABLES)


def test_no_planning_module_is_exempt():
    """The invariant that matters: nothing that computes a plan number is exempt.

    An earlier version of this test asserted a cap on the size of the allowlist.
    That tested an implementation detail -- it broke the moment a legitimate
    storage-layer test was added, and would have been renegotiated every time it
    fired. This asserts the boundary instead, which is the actual rule.
    """
    planning = [
        p for p in ALLOWED
        if p.startswith("planbrain/") and not p.startswith(EXEMPT_PACKAGE_PREFIX)
    ]
    assert planning == []


def test_allowlist_has_no_stale_entries():
    """A path that no longer exists is an exemption nobody is reviewing."""
    assert [p for p in ALLOWED if not (ROOT / p).exists()] == []


def test_registry_matches_the_schema():
    """grains.py is the single source of table names, so it must not drift.

    If a fact table exists in schema.sql but not here, the CI gate silently
    stops covering it -- which is the failure mode this whole module exists to
    prevent.
    """
    import re

    sql = (ROOT / "planbrain" / "facts" / "schema.sql").read_text()
    declared = {}
    for match in re.finditer(
        r"CREATE TABLE (fact_\w+)\s*\((.*?)\n\);", sql, re.DOTALL
    ):
        body = match.group(2)
        cols = [
            line.strip().split()[0]
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith(("PRIMARY", "--"))
        ]
        # Grain keys are every column before the shared bucket_date tail.
        declared[match.group(1)] = tuple(cols[: cols.index("bucket_date")])

    assert declared, "no CREATE TABLE fact_* matched; the parser, not the schema, is broken"
    assert declared == FACT_TABLES

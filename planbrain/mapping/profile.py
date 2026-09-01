"""Column mappings as YAML files a person can read and write.

**Profiles are data, not code.** A SAP or Tally profile is a file we ship, and a
user can hand-write one in a text editor without this application. That is the
whole design constraint, and it is why the format is flat and its field names
are the ones the mapping UI shows.

    name: Tally sales register
    table: history
    source_hint: tally
    sheet: Sheet1
    header_row: 4
    columns:
      sku_id: Item Code
      loc_id: Godown
      bucket_date: Date
      qty: Quantity (Kgs)
    options:
      date_format: dmy

`header_row` is 1-based here, because a user counting rows in Excel counts from
one and a profile they hand-edit should agree with what they see on screen.
Internally everything is 0-based, and the conversion happens at this boundary.

Loading uses `yaml.safe_load`. Plain `yaml.load` executes arbitrary Python from
the document, and a profile is exactly the kind of file that gets emailed
around.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

#: Shipped profiles live here; user profiles alongside their database. Both are
#: just directories of YAML, and nothing distinguishes ours except location.
BUILTIN_DIR = Path(__file__).with_name("profiles")


class ProfileError(ValueError):
    """A profile file is malformed. The message names the file and the field."""


@dataclass
class Profile:
    name: str
    table: str
    columns: dict = field(default_factory=dict)      # canonical -> source header
    sheet: str = None
    header_row: int = None                            # 0-based internally
    options: dict = field(default_factory=dict)
    source_hint: str = ""
    path: Path = None

    def as_dict(self) -> dict:
        out = {"name": self.name, "table": self.table}
        if self.source_hint:
            out["source_hint"] = self.source_hint
        if self.sheet:
            out["sheet"] = self.sheet
        if self.header_row is not None:
            out["header_row"] = self.header_row + 1   # back to 1-based for humans
        out["columns"] = dict(self.columns)
        if self.options:
            out["options"] = dict(self.options)
        return out


def load(path) -> Profile:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path.name} is not valid YAML: {exc}") from exc

    if raw is None:
        raise ProfileError(f"{path.name} is empty")
    if not isinstance(raw, dict):
        raise ProfileError(
            f"{path.name} should be a mapping of fields, not a "
            f"{type(raw).__name__}"
        )

    for required in ("name", "table", "columns"):
        if required not in raw:
            raise ProfileError(f"{path.name} is missing '{required}'")

    columns = raw["columns"]
    if not isinstance(columns, dict):
        raise ProfileError(
            f"{path.name}: 'columns' should map canonical field names to the "
            f"headings in your file, like `sku_id: Item Code`"
        )
    if not columns:
        # An empty mapping would load, apply cleanly, and produce nothing --
        # the empty-result-reads-as-success failure again.
        raise ProfileError(f"{path.name}: 'columns' is empty, so it maps nothing")

    header_row = raw.get("header_row")
    if header_row is not None:
        if not isinstance(header_row, int) or header_row < 1:
            raise ProfileError(
                f"{path.name}: 'header_row' should be the row number as Excel "
                f"shows it, counting from 1"
            )
        header_row -= 1

    return Profile(
        name=str(raw["name"]),
        table=str(raw["table"]),
        columns={str(k): str(v) for k, v in columns.items()},
        sheet=raw.get("sheet"),
        header_row=header_row,
        options=raw.get("options") or {},
        source_hint=str(raw.get("source_hint", "")),
        path=path,
    )


def save(profile: Profile, directory) -> Path:
    """Write a profile as YAML. Returns the path written."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug(profile.name)}.yaml"
    path.write_text(
        yaml.safe_dump(profile.as_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def slug(name: str) -> str:
    kept = [c.lower() if c.isalnum() else "-" for c in name.strip()]
    out = "".join(kept)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "profile"


def discover(*directories) -> list:
    """Every readable profile in the given directories.

    A malformed file is reported rather than skipped: silently ignoring it means
    a user's hand-written profile simply never appears and they have nothing to
    debug from.
    """
    found, problems = [], []
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
            try:
                found.append(load(path))
            except ProfileError as exc:
                problems.append(str(exc))
    return found, problems

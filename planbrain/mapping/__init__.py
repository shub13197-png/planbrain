"""Manual column mapping: the fallback the model will sit on top of.

Deliberately built first and built to be genuinely usable alone. A mapping
assistant that is the only way to import is a product that fails whenever the
assistant is wrong, and nobody can tell when it is wrong without the manual path
to compare against.

* `io` reads a spreadsheet as a raw grid, handling merged cells and leading zeros
* `detect` finds the header row and says why
* `apply` maps, previews and validates, all or nothing
* `profile` stores mappings as YAML a person can hand-write
"""

from .apply import PREVIEW_ROWS, MappingResult, apply_mapping, required_fields, suggest
from .detect import HeaderGuess, inspect, normalise
from .io import UnreadableFile, read_grid, sheet_names
from .profile import BUILTIN_DIR, Profile, ProfileError, discover, load, save, slug

__all__ = [
    "BUILTIN_DIR", "HeaderGuess", "MappingResult", "PREVIEW_ROWS", "Profile",
    "ProfileError", "UnreadableFile", "apply_mapping", "discover", "inspect",
    "load", "normalise", "read_grid", "required_fields", "save", "sheet_names",
    "slug", "suggest",
]

"""Every text file in the repo must be valid UTF-8.

Python 3.14 on Windows still opens files with the locale codepage, so a scripted
edit can round-trip a UTF-8 document into mixed encoding. Worse, str.replace on
a mis-decoded string matches nothing and returns the original *without raising*
-- a silent no-op edit that reports success. That has already happened once to
docs/decisions.md.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".md", ".sql", ".json", ".toml", ".yml", ".yaml", ".cfg", ".txt"}
SKIP = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def _text_files():
    for path in ROOT.rglob("*"):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if SKIP & set(path.relative_to(ROOT).parts):
            continue
        yield path


#: Collected once at import. An empty list would produce zero parametrised
#: tests, and zero tests report as a pass -- so the count is asserted separately.
TEXT_FILES = sorted(_text_files())


def test_the_scan_actually_found_files():
    """A gate that examined nothing is indistinguishable from a gate that passed."""
    assert len(TEXT_FILES) >= 10, f"only {len(TEXT_FILES)} text files found under {ROOT}"


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.name)
def test_file_is_valid_utf8(path):
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(
            f"{path.relative_to(ROOT).as_posix()} is not valid UTF-8 at byte "
            f"{exc.start}: {exc.reason}. Pass encoding='utf-8' explicitly when "
            f"scripting edits on Windows."
        )

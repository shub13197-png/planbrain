"""Row-level validation errors that name the row and the column.

The whole point of the importer. A prospective user is on spreadsheets with no
clean master data, and "import failed: invalid value" is useless to someone
looking at four thousand rows. Every error carries the file, the sheet, the
1-based row number as the spreadsheet shows it, the column name, and the value
that was actually there.

Errors **accumulate**. An import that stops at the first bad row makes the user
fix one cell, re-run, and wait, forty times. Every row is checked and every
problem is reported in one pass.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RowError:
    """One problem, located precisely enough to fix without searching."""

    source: str
    row: int
    column: str
    value: object
    message: str

    def __str__(self) -> str:
        where = f"{self.source} row {self.row}"
        if self.column:
            where += f", column '{self.column}'"
        shown = "" if self.value in (None, "") else f" (got {self.value!r})"
        return f"{where}: {self.message}{shown}"


@dataclass
class ErrorLog:
    """Accumulated problems across a whole import.

    Carries a cap so a systematically broken file -- wrong delimiter, shifted
    header -- reports the first several hundred rather than four thousand
    identical lines. The cap is reported when it bites, because a truncated
    error list that does not say it was truncated is the same silent-success
    failure this project keeps finding.
    """

    errors: list = field(default_factory=list)
    limit: int = 200
    suppressed: int = 0

    def add(self, source, row, column, value, message) -> None:
        if len(self.errors) >= self.limit:
            self.suppressed += 1
            return
        self.errors.append(RowError(source, row, column, value, message))

    def extend(self, other: "ErrorLog") -> None:
        for error in other.errors:
            self.add(error.source, error.row, error.column, error.value, error.message)
        self.suppressed += other.suppressed

    def __bool__(self) -> bool:
        return bool(self.errors)

    def __len__(self) -> int:
        return len(self.errors)

    def report(self) -> str:
        lines = [str(e) for e in self.errors]
        if self.suppressed:
            lines.append(
                f"... and {self.suppressed} more problems not shown. A file this "
                f"broken usually has one cause -- check the header row and the "
                f"delimiter before fixing cells."
            )
        return "\n".join(lines)


class ImportRejected(ValueError):
    """The import found problems and wrote nothing.

    All or nothing on purpose. A partial import leaves the user with a database
    that looks populated and is missing rows they will not find until a plan
    comes out wrong.
    """

    def __init__(self, log: ErrorLog):
        self.log = log
        super().__init__(
            f"{len(log)} problem(s) found; nothing was imported.\n{log.report()}"
        )

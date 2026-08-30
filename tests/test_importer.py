"""The importer, tested on the files a real customer would actually send.

The importer's job is not parsing. It is telling someone maintaining a
four-thousand-row spreadsheet by hand exactly which cell is wrong, all of them
at once, without half-importing the file.
"""

import pytest

from planbrain.importer import (
    ErrorLog,
    ImportRejected,
    load_table,
    validate_cross_references,
)


def _csv(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


PARTS_HEADER = "sku_id,name,level,lead_time_days,safety_stock,lot_policy,lot_qty,unit_cost\n"


# --------------------------------------------------------------------------
# errors name the row and the column
# --------------------------------------------------------------------------

def test_a_clean_file_imports(tmp_path):
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000,SN-150,raw,14,2000,fixed_qty,5000,90\n")
    rows, log = load_table(path, "parts")
    assert not log
    assert rows[0]["sku_id"] == 1000
    assert rows[0]["level"] == "raw"


def test_an_error_names_the_file_row_column_and_value(tmp_path):
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000,SN-150,raw,14,2000,fixed_qty,5000,90\n"
                "1001,SN-500,raw,soon,2000,fixed_qty,5000,90\n")
    rows, log = load_table(path, "parts")

    assert len(log) == 1
    message = str(log.errors[0])
    assert "parts.csv" in message
    assert "row 3" in message, "the row number the spreadsheet shows, not the index"
    assert "lead_time_days" in message
    assert "soon" in message


def test_row_numbers_match_the_spreadsheet(tmp_path):
    """Row 1 is the header, so the first data row is row 2. Off by one here
    sends someone to the wrong line of a four-thousand-row file."""
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "bad,SN-150,raw,14,2000,fixed_qty,5000,90\n")
    _rows, log = load_table(path, "parts")
    assert log.errors[0].row == 2


def test_every_bad_row_is_reported_not_just_the_first(tmp_path):
    """Stopping at the first makes someone fix one cell and re-run, forty times."""
    body = "".join(
        f"{1000 + i},Part {i},raw,oops,0,fixed_qty,100,90\n" for i in range(5)
    )
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER + body)
    _rows, log = load_table(path, "parts")
    assert len(log) == 5


def test_a_bad_row_does_not_discard_the_good_ones(tmp_path):
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000,Good,raw,14,0,fixed_qty,100,90\n"
                "1001,Bad,raw,oops,0,fixed_qty,100,90\n"
                "1002,Also good,raw,21,0,fixed_qty,100,90\n")
    rows, log = load_table(path, "parts")
    assert len(rows) == 2
    assert len(log) == 1


# --------------------------------------------------------------------------
# spreadsheet reality
# --------------------------------------------------------------------------

def test_thousands_separators_and_currency_symbols_are_accepted(tmp_path):
    """A user should not have to reformat a column to import it."""
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                '1000,SN-150,raw,14,"2,000",fixed_qty,"5,000","₹1,250.50"\n')
    rows, log = load_table(path, "parts")
    assert not log
    assert rows[0]["safety_stock"] == 2000.0
    assert rows[0]["unit_cost"] == 1250.50


def test_a_float_that_is_really_a_whole_number_is_accepted(tmp_path):
    """Spreadsheets turn integer columns into floats constantly."""
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000.0,SN-150,raw,14.0,0,fixed_qty,100,90\n")
    rows, log = load_table(path, "parts")
    assert not log
    assert rows[0]["sku_id"] == 1000


def test_a_genuine_fraction_in_an_integer_column_is_refused(tmp_path):
    """14.5 lead-time days means the column was misunderstood, not rounded."""
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000,SN-150,raw,14.5,0,fixed_qty,100,90\n")
    _rows, log = load_table(path, "parts")
    assert "whole number" in str(log.errors[0])


def test_an_ambiguous_date_is_refused_rather_than_guessed(tmp_path):
    """03/04/2026 is 3 April in India and 4 March in America. Guessing wrong
    shifts a demand history by a month and nothing fails."""
    path = _csv(tmp_path, "history.csv",
                "sku_id,loc_id,bucket_date,qty\n1000,11,03/04/2026,50\n")
    _rows, log = load_table(path, "history")
    assert "YYYY-MM-DD" in str(log.errors[0])
    assert "ambiguous" in str(log.errors[0])


def test_yes_and_no_are_understood_however_they_are_written(tmp_path):
    header = "truck_id,plate,capacity_kg,available,ytd_long_haul_km\n"
    path = _csv(tmp_path, "fleet.csv", header +
                "1,RJ14-1,16000,Yes,1000\n"
                "2,RJ14-2,16000,N,2000\n"
                "3,RJ14-3,16000,,3000\n")
    rows, log = load_table(path, "fleet")
    assert not log
    assert [r["available"] for r in rows] == [True, False, True]


def test_a_nan_is_refused(tmp_path):
    """qty has no constraint stopping a NaN, and one poisons every sum in silence."""
    path = _csv(tmp_path, "history.csv",
                "sku_id,loc_id,bucket_date,qty\n1000,11,2026-03-02,nan\n")
    _rows, log = load_table(path, "history")
    assert "finite" in str(log.errors[0])


def test_blank_spacer_rows_are_ignored_not_reported(tmp_path):
    """Real spreadsheets have them and they are not errors."""
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000,SN-150,raw,14,0,fixed_qty,100,90\n")
    rows, log = load_table(path, "parts")
    assert len(rows) == 1 and not log


# --------------------------------------------------------------------------
# structural problems, reported against the header
# --------------------------------------------------------------------------

def test_a_missing_column_is_reported_once_against_the_header(tmp_path):
    """Not once per row. A missing column would otherwise bury every real error."""
    path = _csv(tmp_path, "parts.csv",
                "sku_id,name,level\n1000,SN-150,raw\n1001,SN-500,raw\n")
    rows, log = load_table(path, "parts")
    assert rows == []
    assert len(log) == 1
    assert log.errors[0].row == 1
    assert "lead_time_days" in log.errors[0].column


def test_an_empty_file_says_so(tmp_path):
    path = _csv(tmp_path, "parts.csv", "")
    _rows, log = load_table(path, "parts")
    assert "empty" in str(log.errors[0])


def test_duplicate_keys_are_reported_with_the_row_they_duplicate(tmp_path):
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER +
                "1000,SN-150,raw,14,0,fixed_qty,100,90\n"
                "1000,SN-150 again,raw,14,0,fixed_qty,100,90\n")
    rows, log = load_table(path, "parts")
    assert len(rows) == 1
    assert "duplicates row 2" in str(log.errors[0])


def test_a_systematically_broken_file_is_truncated_and_says_so(tmp_path):
    """Four thousand identical errors help nobody, and a silently truncated
    list is the same failure this project keeps finding."""
    body = "".join(f"{i},Part,raw,oops,0,fixed_qty,100,90\n" for i in range(1000, 1400))
    path = _csv(tmp_path, "parts.csv", PARTS_HEADER + body)
    _rows, log = load_table(path, "parts")
    assert len(log) == log.limit
    assert log.suppressed > 0
    assert "not shown" in log.report()
    assert "one cause" in log.report()


def test_an_unknown_table_is_refused():
    with pytest.raises(ValueError, match="unknown table"):
        load_table("nowhere.csv", "invoices")


# --------------------------------------------------------------------------
# the tables have to agree with each other
# --------------------------------------------------------------------------

def test_a_bom_referencing_an_unknown_sku_is_caught():
    """The commonest real failure: two sheets maintained by two people."""
    tables = {
        "parts": [{"sku_id": 1000}, {"sku_id": 2000}],
        "bom": [{"parent_sku_id": 2000, "child_sku_id": 9999}],
    }
    log = validate_cross_references(tables)
    assert len(log) == 1
    assert "not in the part master" in str(log.errors[0])
    assert log.errors[0].column == "child_sku_id"


def test_a_part_that_is_its_own_component_is_caught():
    """netreq would not crash on this. It would fail to terminate."""
    tables = {
        "parts": [{"sku_id": 2000}],
        "bom": [{"parent_sku_id": 2000, "child_sku_id": 2000}],
    }
    log = validate_cross_references(tables)
    assert "component of itself" in str(log.errors[0])


def test_history_and_routings_are_checked_against_the_part_master():
    tables = {
        "parts": [{"sku_id": 1000}],
        "routings": [{"sku_id": 5555, "resource_id": 1}],
        "history": [{"sku_id": 6666, "loc_id": 11, "bucket_date": None}],
    }
    log = validate_cross_references(tables)
    assert len(log) == 2


def test_cross_checks_are_skipped_when_the_part_master_did_not_parse():
    """Otherwise one bad cell in parts.csv produces a cascade of phantom
    "not in the part master" errors for SKUs that are in it and simply failed
    validation. A user chasing those is worse off than one told to fix parts first.
    """
    tables = {
        "parts": [{"sku_id": 1000}],  # 2000 and 3000 failed to parse
        "bom": [{"parent_sku_id": 3000, "child_sku_id": 2000}],
    }
    cascade = validate_cross_references(tables)
    assert len(cascade) == 2, "without the guard, both ids look missing"

    suppressed = validate_cross_references(tables, clean=set())
    assert len(suppressed) == 1
    assert "fix those rows" in str(suppressed.errors[0])


def test_cross_checks_still_run_when_the_part_master_is_clean():
    tables = {
        "parts": [{"sku_id": 1000}],
        "bom": [{"parent_sku_id": 1000, "child_sku_id": 9999}],
    }
    log = validate_cross_references(tables, clean={"parts", "bom"})
    assert len(log) == 1
    assert "not in the part master" in str(log.errors[0])


def test_consistent_tables_produce_no_errors():
    tables = {
        "parts": [{"sku_id": 1000}, {"sku_id": 2000}],
        "bom": [{"parent_sku_id": 2000, "child_sku_id": 1000}],
        "routings": [{"sku_id": 2000, "resource_id": 1}],
    }
    assert not validate_cross_references(tables)


# --------------------------------------------------------------------------
# all or nothing
# --------------------------------------------------------------------------

def test_rejection_says_nothing_was_imported():
    """A partial import leaves a database that looks populated and is missing
    rows nobody finds until a plan comes out wrong."""
    log = ErrorLog()
    log.add("parts.csv", 7, "lead_time_days", "soon", "should be a whole number")
    error = ImportRejected(log)
    assert "nothing was imported" in str(error)
    assert "row 7" in str(error)

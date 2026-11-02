import csv
import io
import zipfile

import pytest

from copilot.data.importing import (MAX_EXPANDED_BYTES, MAX_FILE_BYTES, XLSX_TYPE, import_csv_tables, import_workbook)
from copilot.models import DataError
from tests.workbook_fixture import workbook_bytes


def csv_files(dataset):
    result = {}
    for table, records in dataset.payload().items():
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
        result[table] = (f"{table}.csv", "text/csv", stream.getvalue().encode())
    return result


def test_csv_roundtrip_bom_whitespace_and_categories(tiny_dataset):
    files = csv_files(tiny_dataset)
    name, mime, content = files["students"]
    content = content.replace(b"student_id", b" Student_ID ").replace(b"S0001,", b" S0001 ,")
    files["students"] = (name, mime, b"\xef\xbb\xbf" + content + b"\n")
    imported, report = import_csv_tables(files)
    assert imported.version == tiny_dataset.version
    assert imported.students[0].display_name == "Synthetic Student 0001"
    assert report["blank_rows_removed"]["students"] == 1


def test_real_excel_roundtrip_reads_five_sheets(tiny_dataset):
    content = workbook_bytes(tiny_dataset.payload())
    imported, report = import_workbook("synthetic.xlsx", XLSX_TYPE, content)
    assert imported.version == tiny_dataset.version
    assert report["format"] == "xlsx"


@pytest.mark.parametrize("replacement", [b"status, STATUS", b"status,email"])
def test_duplicate_or_unknown_headers_rejected(tiny_dataset, replacement):
    files = csv_files(tiny_dataset)
    name, mime, content = files["grade_records"]
    files["grade_records"] = (name, mime, content.replace(b"status,score_cents", replacement))
    with pytest.raises(DataError):
        import_csv_tables(files)


@pytest.mark.parametrize("bad_value", [b"10001", b"NaN", b"Infinity", b"60.25", b"=SUM(1)", b"-1"])
def test_bad_score_cells_rejected_without_echoing_values(tiny_dataset, bad_value):
    files = csv_files(tiny_dataset)
    name, mime, content = files["grade_records"]
    files["grade_records"] = (name, mime, content.replace(b",6000\n", b"," + bad_value + b"\n"))
    with pytest.raises(DataError) as error:
        import_csv_tables(files)
    assert bad_value.decode() not in str(error.value)


def test_orphan_rows_and_real_names_rejected(tiny_dataset):
    files = csv_files(tiny_dataset)
    name, mime, content = files["students"]
    files["students"] = (name, mime, content.replace(b"C001", b"C999"))
    with pytest.raises(DataError, match="unknown class"):
        import_csv_tables(files)
    files["students"] = (name, mime, content.replace(b"Synthetic Student 0001", b"Real Person"))
    with pytest.raises(DataError, match="typed record"):
        import_csv_tables(files)


@pytest.mark.parametrize("filename,mime,content", [
    ("../../students.csv", "text/csv", b"abc"),
    ("students.pkl", "text/csv", b"abc"),
    ("students.csv", "application/octet-stream", b"abc"),
    ("students.csv", "text/csv", b"\x80\xff"),
    ("students.csv", "text/csv", b"\x00"),
    ("students.csv", "text/csv", b""),
])
def test_upload_file_validation(tiny_dataset, filename, mime, content):
    files = csv_files(tiny_dataset)
    files["students"] = (filename, mime, content)
    with pytest.raises(DataError):
        import_csv_tables(files)


def test_file_size_limit_and_missing_table(tiny_dataset):
    files = csv_files(tiny_dataset)
    files["students"] = ("students.csv", "text/csv", b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(DataError) as error:
        import_csv_tables(files)
    assert error.value.status == 413
    files.pop("classes")
    with pytest.raises(DataError, match="five"):
        import_csv_tables(files)


@pytest.mark.parametrize("flags", [{"formula": True}, {"external": True}])
def test_excel_formula_and_external_links_rejected(tiny_dataset, flags):
    with pytest.raises(DataError):
        import_workbook("test.xlsx", XLSX_TYPE, workbook_bytes(tiny_dataset.payload(), **flags))


def test_excel_expansion_limit_and_disguised_files(tiny_dataset):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("huge.xml", b"x" * (MAX_EXPANDED_BYTES + 1))
    with pytest.raises(DataError) as error:
        import_workbook("huge.xlsx", XLSX_TYPE, content.getvalue())
    assert error.value.status == 413
    with pytest.raises(DataError):
        import_workbook("fake.xlsx", XLSX_TYPE, b"not a workbook")


def test_duplicate_rows_are_not_silently_dropped(tiny_dataset):
    files = csv_files(tiny_dataset)
    name, mime, content = files["students"]
    files["students"] = (name, mime, content + content.splitlines()[1] + b"\n")
    with pytest.raises(DataError, match="Duplicate primary"):
        import_csv_tables(files)


def test_excel_entity_declaration_in_relationships_is_a_validation_error(tiny_dataset):
    source = workbook_bytes(tiny_dataset.payload())
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as before, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as after:
        for entry in before.infolist():
            content = before.read(entry)
            if entry.filename == "xl/_rels/workbook.xml.rels":
                content = b'<!DOCTYPE Relationships [<!ENTITY injected "blocked">]>' + content
            after.writestr(entry.filename, content)
    with pytest.raises(DataError, match="Invalid XLSX"):
        import_workbook("entities.xlsx", XLSX_TYPE, output.getvalue())


def test_physical_row_limit_is_enforced(tiny_dataset, monkeypatch):
    from copilot.data import importing
    monkeypatch.setitem(importing.MAX_ROWS, "students", 2)
    with pytest.raises(DataError) as error:
        import_csv_tables(csv_files(tiny_dataset))
    assert error.value.status == 413

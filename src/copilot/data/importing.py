"""Bounded CSV / XLSX import with exact headers and explicit typed cleaning.

Only the five normalized tables are accepted. Imports are local-only, even if
their labels look synthetic; display-name checks cannot establish provenance.
"""
import csv
import io
import math
from pathlib import PurePosixPath
import re
import zipfile

from defusedxml import ElementTree as SafeXML
from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.xml import DEFUSEDXML
from pydantic import ValidationError

from copilot.models import DataError, Dataset, RECORD_TYPES, TABLE_NAMES

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ROWS = {"classes": 100, "students": 5000, "courses": 500, "enrollments": 50000, "grade_records": 50000}
INTEGER_FIELDS = {"grade_level", "credits", "score_cents"}
CSV_TYPES = {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def validate_file(filename: str, content_type: str, content: bytes, suffix: str):
    if not filename or len(filename) > 120 or "/" in filename or "\\" in filename or "\x00" in filename:
        raise DataError("A plain, bounded filename is required", code="invalid_filename")
    if not content or len(content) > MAX_FILE_BYTES:
        raise DataError("File must be nonempty and at most 5 MiB", code="file_size", status=413 if content else 422)
    allowed = CSV_TYPES if suffix == ".csv" else {XLSX_TYPE}
    if PurePosixPath(filename).suffix.lower() != suffix or content_type.split(";")[0].strip().lower() not in allowed:
        raise DataError(f"Expected {suffix} with its supported media type", code="unsupported_media_type", status=415)


def _clean(field: str, value):
    if field in INTEGER_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if type(value) is int:
            return value
        if type(value) is float and math.isfinite(value) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
            return int(value.strip())
        raise DataError("Integer columns must contain finite whole numbers")
    if not isinstance(value, str):
        raise DataError("Identifier and categorical columns must contain text")
    value = value.strip()
    return value.lower() if field == "status" else value


def _records(table: str, rows):
    model = RECORD_TYPES[table]
    header = next(rows, None)
    if header is None or any(not isinstance(c, str) for c in header):
        raise DataError(f"{table}: a text header is required")
    headers = [h.strip().lower() for h in header]
    if len(headers) != len(set(headers)):
        raise DataError(f"{table}: duplicate normalized headers")
    required = {name for name, field in model.model_fields.items() if field.is_required()}
    if not required <= set(headers) or set(headers) - set(model.model_fields):
        raise DataError(f"{table}: required headers are {', '.join(sorted(required))}; only declared optional columns are accepted")
    records, blank_rows = [], 0
    for row_number, row in enumerate(rows, 2):
        if row_number > MAX_ROWS[table] + 1:
            raise DataError(f"{table}: row limit exceeded", code="row_limit", status=413)
        if all(v is None or isinstance(v, str) and not v.strip() for v in row):
            blank_rows += 1
            continue
        if len(row) != len(headers):
            raise DataError(f"{table} row {row_number}: incorrect field count")
        if any(isinstance(v, str) and (len(v) > 200 or "\x00" in v) for v in row):
            raise DataError(f"{table} row {row_number}: invalid field length or NUL")
        try:
            cleaned = {field: _clean(field, value) for field, value in zip(headers, row)}
            records.append(model.model_validate(cleaned))
        except (ValidationError, DataError, ValueError) as exc:
            # Do not echo invalid names, scores, cell content or Pydantic input values.
            raise DataError(f"{table} row {row_number}: invalid typed record") from exc
    if not records:
        raise DataError(f"{table}: no nonblank data rows")
    return tuple(records), blank_rows


def import_csv_tables(files: dict[str, tuple[str, str, bytes]]) -> tuple[Dataset, dict]:
    if set(files) != set(TABLE_NAMES):
        raise DataError("Supply exactly the five normalized CSV tables")
    tables, blanks = {}, {}
    for table in TABLE_NAMES:
        filename, media_type, content = files[table]
        validate_file(filename, media_type, content, ".csv")
        try:
            text = content.decode("utf-8-sig")
            if "\x00" in text:
                raise DataError(f"{table}: NUL bytes are not allowed")
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            tables[table], blanks[table] = _records(table, iter(reader))
        except (UnicodeError, csv.Error) as exc:
            raise DataError(f"{table}: expected well-formed UTF-8 CSV") from exc
    return Dataset(**tables).validate(), {"format": "csv", "blank_rows_removed": blanks}


def _validate_xlsx(content: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 300 or sum(x.file_size for x in entries) > MAX_EXPANDED_BYTES:
                raise DataError("Workbook expanded size or entry count exceeds limits", code="workbook_limits", status=413)
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or "[Content_Types].xml" not in names:
                raise DataError("Invalid XLSX package")
            for entry in entries:
                name = entry.filename.lower()
                if (entry.flag_bits & 1 or ".." in PurePosixPath(name).parts or name.startswith("/") or "\\" in name
                        or any(token in name for token in ("vbaproject", "externallinks", "embeddings", "connections.xml"))
                        or name.endswith(".bin")):
                    raise DataError("Encrypted, embedded or externally linked workbooks are unsupported")
                if name.endswith(".rels"):
                    if entry.file_size > 1024 * 1024:
                        raise DataError("Workbook relationship metadata exceeds limits")
                    root = SafeXML.fromstring(archive.read(entry), forbid_dtd=True)
                    if any(r.attrib.get("TargetMode", "").lower() == "external" for r in root):
                        raise DataError("External workbook relationships are unsupported")
    except (zipfile.BadZipFile, RuntimeError, SafeXML.ParseError, DefusedXmlException) as exc:
        raise DataError("Invalid XLSX package") from exc


def import_workbook(filename: str, content_type: str, content: bytes) -> tuple[Dataset, dict]:
    validate_file(filename, content_type, content, ".xlsx")
    _validate_xlsx(content)
    if not DEFUSEDXML:
        raise RuntimeError("Hardened XML parser is required")
    workbook = None
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        sheets = {s.title.strip().lower(): s for s in workbook.worksheets}
        if len(sheets) != len(workbook.worksheets) or set(sheets) != set(TABLE_NAMES):
            raise DataError("Workbook must contain exactly the five normalized table sheets")
        tables, blanks = {}, {}
        for table, sheet in sheets.items():
            if sheet.sheet_state != "visible":
                raise DataError("Hidden sheets are unsupported")
            sheet.reset_dimensions()

            def rows():
                for row in sheet.iter_rows():
                    if any(cell.data_type == "f" for cell in row):
                        raise DataError("Spreadsheet formulas are unsupported; supply literal values")
                    yield tuple(cell.value for cell in row)

            tables[table], blanks[table] = _records(table, iter(rows()))
        return Dataset(**tables).validate(), {"format": "xlsx", "blank_rows_removed": blanks}
    except DataError:
        raise
    except Exception as exc:
        raise DataError("Workbook could not be parsed safely") from exc
    finally:
        if workbook is not None:
            workbook.close()

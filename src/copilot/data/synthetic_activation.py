"""Server-derived synthetic activation, separate from untrusted file uploads.

Only options are accepted. Both preview and commit generate the workbook on the
server, so a client filename, source flag or uploaded byte sequence can never
grant external-model access to private imported records.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated

from pydantic import Field, field_validator

from copilot.data.college import CollegeCommitResult, CollegePreview, parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.models import DataError, Record, TABLE_NAMES

PREVIEW_SECONDS = 900


class SyntheticJSONOptions(SyntheticOptions):
    """Explicit JSON tuple normalization without weakening domain validation."""
    @field_validator("majors", mode="before")
    @classmethod
    def json_major_array(cls, value):
        return tuple(value) if isinstance(value, list) else value


class SyntheticCommitRequest(Record):
    options: SyntheticJSONOptions
    preview_token: Annotated[str, Field(min_length=1, max_length=1500)]
    confirmed: bool = False


class SyntheticActivationService:
    def __init__(self, database):
        self.database = database
        self._secret = secrets.token_bytes(32)

    @staticmethod
    def _options_hash(options):
        return hashlib.sha256(json.dumps(options.model_dump(mode="json"), sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _parse(options):
        if not isinstance(options, SyntheticOptions):
            raise TypeError("options must be validated SyntheticOptions")
        generated = generate_college_workbook(options)
        return parse_college_workbook(generated.filename, XLSX_TYPE, generated.content)

    def preview(self, options: SyntheticOptions) -> CollegePreview:
        try:
            before = self.database.overview()
        except DataError as exc:
            if exc.code != "no_dataset":
                raise
            before = None
        parsed = self._parse(options)
        version = before.dataset_version if before else None
        token = None
        if parsed.dataset is not None:
            payload = {"kind": "server-generated-demo-v1", "options": self._options_hash(options),
                       "version": version, "normalized": parsed.dataset.version,
                       "expires": int(time.time()) + PREVIEW_SECONDS}
            encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
            token = encoded + "." + hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return parsed.preview.model_copy(update={"preview_token": token, "expected_version": version,
            "before_counts": before.counts if before else {table: 0 for table in TABLE_NAMES},
            "expires_in_seconds": PREVIEW_SECONDS})

    def commit(self, options: SyntheticOptions, preview_token: str, confirmed: bool) -> CollegeCommitResult:
        if confirmed is not True:
            raise DataError("请先核对合成示例，并明确确认替换当前数据。", code="confirmation_required")
        try:
            if not isinstance(preview_token, str) or len(preview_token) > 1500:
                raise ValueError
            encoded, signature = preview_token.split(".")
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError
            payload = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
            if payload["kind"] != "server-generated-demo-v1" or payload["options"] != self._options_hash(options):
                raise ValueError
            if type(payload["expires"]) is not int:
                raise ValueError
            if payload["expires"] < time.time():
                raise DataError("合成示例预览已过期，请重新生成预览。", code="preview_expired", status=409)
        except DataError:
            raise
        except (ValueError, KeyError, TypeError):
            raise DataError("合成示例凭据无效或设置已改变，请重新预览。", code="preview_invalid", status=409) from None
        parsed = self._parse(options)
        if parsed.dataset is None or parsed.dataset.version != payload["normalized"]:
            raise DataError("这份合成示例仍有阻止项，请调整设置后重新预览。", code="college_not_ready", status=409)
        # Replace every business table with exclusively server-derived records.
        # Inheriting a previous private course catalog would contaminate trusted
        # synthetic provenance, even if no old student rows remained.
        info = self.database.replace(parsed.dataset, expected_version=payload["version"],
            expected_empty=payload["version"] is None, source="generated", seed=options.seed, preserve_courses=False)
        return CollegeCommitResult(dataset=info,
            imported_counts={table: len(getattr(parsed.dataset, table)) for table in TABLE_NAMES},
            retained_course_count=0)

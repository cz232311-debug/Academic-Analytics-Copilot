"""Preview/confirmation boundary; no application-managed workbook persistence.

The HTTP framework may spool uploads to its temporary directory. API handlers
always close UploadFile; neither this service nor the adapter saves originals.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from copilot.data.college import CollegeCommitResult, parse_college_workbook
from copilot.models import DataError


class CollegeImportService:
    def __init__(self, database):
        self.database = database
        self._secret = secrets.token_bytes(32)

    def preview(self, filename, content_type, content, *, duplicate_policy="block"):
        before = self.database.overview()
        parsed = parse_college_workbook(filename, content_type, content, duplicate_policy=duplicate_policy)
        token = None
        if parsed.dataset is not None:
            payload = {"file": hashlib.sha256(content).hexdigest(), "version": before.dataset_version,
                       "normalized": parsed.dataset.version, "policy": duplicate_policy, "expires": int(time.time()) + 900}
            encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
            token = encoded + "." + hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return parsed.preview.model_copy(update={"preview_token": token, "expected_version": before.dataset_version,
                                                "before_counts": before.counts})

    def commit(self, filename, content_type, content, *, preview_token, confirmed):
        if confirmed is not True:
            raise DataError("请先检查预览并明确确认导入", code="confirmation_required")
        try:
            if not isinstance(preview_token, str) or len(preview_token) > 1500:
                raise ValueError
            encoded, signature = preview_token.split(".")
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError
            payload = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
            if payload["expires"] < time.time():
                raise DataError("预览已过期，请重新解析预览", code="preview_expired", status=409)
            if payload["file"] != hashlib.sha256(content).hexdigest():
                raise ValueError
        except DataError:
            raise
        except (ValueError, KeyError, TypeError):
            raise DataError("预览凭据无效或文件已改变，请重新预览", code="preview_invalid", status=409) from None
        parsed = parse_college_workbook(filename, content_type, content, duplicate_policy=payload["policy"])
        if parsed.dataset is None or parsed.dataset.version != payload["normalized"]:
            raise DataError("解析结果无法提交或已改变，请重新预览", code="college_not_ready", status=409)
        # CAS guard and preserving the directory both happen inside the write transaction.
        info = self.database.replace(parsed.dataset, expected_version=payload["version"], preserve_courses=True,
                                     source="uploaded_local_only")
        return CollegeCommitResult(dataset=info, imported_counts={
            name: len(getattr(parsed.dataset, name))
            for name in ("classes", "students", "courses", "enrollments", "grade_records")},
            retained_course_count=info.counts["courses"] - len(parsed.dataset.courses))

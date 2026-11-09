"""Preview/confirmation boundary with explicit-confirmation private retention.

The HTTP framework may spool uploads to its temporary directory. API handlers
always close UploadFile. Preview never persists originals; confirmed imports
retain originals and de-identified receipts in the local private archive.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from copilot.data.college import CollegeCommitResult, parse_college_workbook
from copilot.data.import_archive import ImportArchive
from copilot.models import DataError, TABLE_NAMES


class CollegeImportService:
    def __init__(self, database):
        self.database = database
        self._secret = secrets.token_bytes(32)
        self.archive = ImportArchive(database.path.parent / ".import-history")

    def preview(self, filename, content_type, content, *, duplicate_policy="block"):
        try:
            before = self.database.overview()
        except DataError as exc:
            if exc.code != "no_dataset":
                raise
            before = None
        before_version = before.dataset_version if before is not None else None
        before_counts = before.counts if before is not None else {name: 0 for name in TABLE_NAMES}
        parsed = parse_college_workbook(filename, content_type, content, duplicate_policy=duplicate_policy)
        token = None
        if parsed.dataset is not None:
            payload = {"file": hashlib.sha256(content).hexdigest(), "version": before_version,
                       "normalized": parsed.dataset.version, "policy": duplicate_policy, "expires": int(time.time()) + 900}
            encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
            token = encoded + "." + hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return parsed.preview.model_copy(update={"preview_token": token, "expected_version": before_version,
                                                "before_counts": before_counts})

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
        # Preserve the original before changing the database. Failure here has
        # no database side effect; filenames never determine an archive path.
        staged = self.archive.stage(content, parsed.preview, expected_version=payload["version"],
                                    duplicate_policy=payload["policy"])
        try:
            # CAS guard and preserving the directory happen in the write transaction.
            info = self.database.replace(parsed.dataset, expected_version=payload["version"], expected_empty=payload["version"] is None,
                                         preserve_courses=True, source="uploaded_local_only")
        except Exception:
            try:
                self.archive.discard(staged)
            except (OSError, ValueError):
                raise DataError("导入未完成，私有暂存原件需要本地清理", code="archive_cleanup_failed", status=503) from None
            raise
        archive_status = "complete"
        try:
            self.archive.finalize(staged, dataset_version=info.dataset_version)
        except (OSError, ValueError):
            # The transaction has already committed. Never return an error
            # implying rollback; keep the staged original and disclose pending.
            archive_status = "pending"
        return CollegeCommitResult(dataset=info, imported_counts={
            name: len(getattr(parsed.dataset, name))
            for name in ("classes", "students", "courses", "enrollments", "grade_records")},
            retained_course_count=info.counts["courses"] - len(parsed.dataset.courses),
            archive_id=staged.archive_id, archive_status=archive_status)

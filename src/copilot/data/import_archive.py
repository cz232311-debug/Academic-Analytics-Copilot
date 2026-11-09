"""Private local originals and de-identified cleaning/confirmation receipts.

The upload filename is never a path component. Only an explicit confirmed import
creates this archive. These files must be excluded from Git and external models.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from copilot.models import DataError


@dataclass(frozen=True)
class StagedImport:
    archive_id: str
    path: Path


class ImportArchive:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    @staticmethod
    def _write_file(path: Path, content: bytes):
        # Exclusive creation prevents accidentally overwriting any existing file.
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _json_bytes(value):
        return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def _checked_pending_path(self, staged: StagedImport) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", staged.archive_id):
            raise ValueError("Invalid archive identifier")
        expected = self.root / (".pending-" + staged.archive_id)
        resolved = staged.path.resolve()
        if resolved != expected or resolved.parent != self.root:
            raise ValueError("Archive staging path is outside its private root")
        return resolved

    def discard(self, staged: StagedImport):
        # Delete only the exact directory created by this import. Resolve and
        # validate the absolute target before performing a recursive cleanup.
        path = self._checked_pending_path(staged)
        if path.exists():
            shutil.rmtree(path)

    def stage(self, content: bytes, preview, *, expected_version, duplicate_policy) -> StagedImport:
        archive_id = uuid4().hex
        staged = StagedImport(archive_id, self.root / (".pending-" + archive_id))
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            staged.path.mkdir(mode=0o700)
            self._checked_pending_path(staged)
            self._write_file(staged.path / "original.xlsx", content)
            summary = {
                "archive_id": archive_id,
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "original_sha256": hashlib.sha256(content).hexdigest(),
                "expected_version": expected_version,
                "duplicate_policy": duplicate_policy,
                "counts": preview.counts,
                "issue_counts": preview.issue_counts,
                # Messages and row previews are intentionally omitted. Issues
                # remain traceable by their category and source coordinates.
                "issues": [{key: getattr(issue, key) for key in ("code", "severity", "row", "column")}
                           for issue in preview.issues],
                "assumptions": preview.assumptions,
            }
            self._write_file(staged.path / "cleaning.json", self._json_bytes(summary))
            return staged
        except (OSError, ValueError):
            try:
                self.discard(staged)
            except (OSError, ValueError):
                pass  # Preserve a failed private staging file rather than guess another path.
            raise DataError("无法保留本地导入原件，数据库未提交；请检查本地保存位置", code="archive_unavailable", status=503) from None

    def finalize(self, staged: StagedImport, *, dataset_version):
        path = self._checked_pending_path(staged)
        receipt = {"archive_id": staged.archive_id, "status": "committed", "confirmed": True,
                   "committed_at": datetime.now(timezone.utc).isoformat(),
                   "dataset_version": dataset_version, "source": "uploaded_local_only"}
        self._write_file(path / "confirmation.json", self._json_bytes(receipt))
        # If receipt writing or this rename fails, the service must report
        # pending; original.xlsx and cleaning.json remain available locally.
        path.rename(self.root / staged.archive_id)

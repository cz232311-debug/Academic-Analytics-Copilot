"""Local FastAPI application. The first release has no external LLM calls."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from copilot.analytics import AnalyticsEngine
from copilot.data.generate import DEFAULT_SEED, generate_dataset
from copilot.data.importing import MAX_FILE_BYTES, import_csv_tables, import_workbook
from copilot.models import ClassId, CourseId, DataError, TABLE_NAMES, Term
from copilot.schemas import (Catalog, ClassComparison, DatasetInfo, ErrorDetail, ErrorResponse,
                             ImportResult, Scope, Statistics)
from copilot.storage import Database

MAX_REQUEST_BYTES = 26 * 1024 * 1024


def error_response(code: str, message: str, status: int):
    return JSONResponse(ErrorResponse(error=ErrorDetail(code=code, message=message)).model_dump(mode="json"), status_code=status)


class UploadBodyLimit:
    """Limit the actual multipart bytes before the framework spools uploaded files."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/datasets/import":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
            if declared < 0:
                raise ValueError
        except ValueError:
            return await error_response("invalid_length", "Invalid Content-Length", 400)(scope, receive, send)
        if declared > self.limit:
            return await error_response("request_too_large", "Upload request exceeds its byte limit", 413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.limit:
                return await error_response("request_too_large", "Upload request exceeds its byte limit", 413)(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def read_upload(file: UploadFile):
    content = bytearray()
    while chunk := await file.read(64 * 1024):
        content.extend(chunk)
        if len(content) > MAX_FILE_BYTES:
            raise DataError("File exceeds 5 MiB", code="file_size", status=413)
    return file.filename or "", file.content_type or "", bytes(content)


def create_app(database_path: str | Path | None = None, *, bootstrap_demo=True, max_request_bytes=MAX_REQUEST_BYTES):
    store = Database(database_path or os.environ.get("ACADEMIC_DB_PATH", "data/academic.sqlite3"))
    engine = AnalyticsEngine(store)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        if bootstrap_demo and not store.has_dataset():
            store.replace(generate_dataset(), source="generated", seed=DEFAULT_SEED)
        yield

    application = FastAPI(title="Academic Analytics Copilot", version="0.1.0",
                          description="Synthetic academic analytics. Deterministic statistics; no LLM integration in this release.",
                          lifespan=lifespan)
    application.state.database = store
    application.add_middleware(UploadBodyLimit, limit=max_request_bytes)

    @application.exception_handler(DataError)
    async def data_error(request: Request, exc: DataError):
        return error_response(exc.code, str(exc), exc.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response("invalid_request", "Request fields failed schema validation; see /docs for the contract", 422)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        response = error_response("http_error", "HTTP request could not be processed", exc.status_code)
        response.headers.update(exc.headers or {})
        return response

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return error_response("internal_error", "Unexpected server error", 500)

    @application.get("/health")
    def health():
        return {"status": "ok", "dataset_active": store.has_dataset(), "version": "0.1.0"}

    @application.get("/api/dataset", response_model=DatasetInfo)
    def dataset():
        return store.overview()

    @application.get("/api/catalog", response_model=Catalog)
    def catalog():
        return store.catalog()

    @application.get("/api/analytics/summary", response_model=Statistics)
    def summary(scope: Annotated[Scope, Query()]):
        return engine.summary(scope)

    @application.get("/api/analytics/compare-classes", response_model=ClassComparison)
    def compare_classes(course_id: CourseId, class_ids: Annotated[list[ClassId], Query(min_length=2, max_length=40)],
                        term: Term = "2026-T1"):
        return engine.compare_classes(course_id, class_ids, term)

    @application.post("/api/datasets/import", response_model=ImportResult)
    async def import_dataset(request: Request,
                             classes: UploadFile | None = File(default=None),
                             students: UploadFile | None = File(default=None),
                             courses: UploadFile | None = File(default=None),
                             enrollments: UploadFile | None = File(default=None),
                             grade_records: UploadFile | None = File(default=None),
                             workbook: UploadFile | None = File(default=None)):
        uploads = dict(zip(TABLE_NAMES, (classes, students, courses, enrollments, grade_records)))
        all_uploads = [f for f in [*uploads.values(), workbook] if f is not None]
        try:
            form = await request.form()
            if set(form) - {*TABLE_NAMES, "workbook"} or any(len(form.getlist(k)) != 1 for k in form):
                raise DataError("Unexpected or duplicate multipart fields")
            if workbook is not None:
                if any(f is not None for f in uploads.values()):
                    raise DataError("Choose one workbook or five CSV files")
                parsed, report = await run_in_threadpool(import_workbook, *await read_upload(workbook))
            else:
                if any(f is None for f in uploads.values()):
                    raise DataError("Supply all five CSV files or one workbook")
                files = {table: await read_upload(file) for table, file in uploads.items()}
                parsed, report = await run_in_threadpool(import_csv_tables, files)
            info = await run_in_threadpool(store.replace, parsed, source="uploaded_local_only")
            return ImportResult(dataset=info, **report)
        finally:
            for file in all_uploads:
                await file.close()

    return application


app = create_app()

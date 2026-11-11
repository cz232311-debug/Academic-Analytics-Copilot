"""Local FastAPI application with an explicitly configured function-calling agent."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from copilot.analytics import AnalyticsEngine
from copilot.agent.models import AgentError, AgentResponse, QueryRequest
from copilot.agent.provider import LLMSettings, build_provider
from copilot.agent.service import AgentService, MAX_MODEL_TURNS, MAX_TOOL_CALLS, configure_logging
from copilot.agent.semantics import ResolvedIntent, resolve_question
from copilot.agent.tools import TOOLS
from copilot.data.generate import DEFAULT_SEED, generate_dataset
from copilot.data.importing import MAX_FILE_BYTES, import_csv_tables, import_workbook
from copilot.data.college import CollegeCommitResult, CollegePreview, DuplicatePolicy
from copilot.data.college_service import CollegeImportService
from copilot.data.synthetic_activation import SyntheticActivationService, SyntheticCommitRequest, SyntheticJSONOptions
from copilot.data.synthetic_export import XLSX_MEDIA_TYPE, build_synthetic_export
from copilot.models import ClassId, CourseId, DataError, TABLE_NAMES, Term
from copilot.schemas import (Catalog, ClassComparison, CourseReport, CourseReportQuery, DatasetInfo, ErrorDetail, ErrorResponse,
                             ImportResult, ReportingCatalog, ReportScope, Scope, Statistics)
from copilot.storage import Database

MAX_REQUEST_BYTES = 26 * 1024 * 1024


def error_response(code: str, message: str, status: int):
    return JSONResponse(ErrorResponse(error=ErrorDetail(code=code, message=message)).model_dump(mode="json"), status_code=status)


class UploadBodyLimit:
    """Limit the actual multipart bytes before the framework spools uploaded files."""
    def __init__(self, app, limit, path="/api/datasets/import"):
        self.app, self.limit, self.path = app, limit, path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
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


def create_app(database_path: str | Path | None = None, *, bootstrap_demo=True, max_request_bytes=MAX_REQUEST_BYTES,
               agent_provider=None, agent_timeout_seconds=None):
    store = Database(database_path or os.environ.get("ACADEMIC_DB_PATH", "data/academic.sqlite3"))
    engine = AnalyticsEngine(store)
    college_import = CollegeImportService(store)
    synthetic_import = SyntheticActivationService(store)

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        store.initialize()
        if bootstrap_demo and not store.has_dataset():
            store.replace(generate_dataset(reporting_metadata=True), source="generated", seed=DEFAULT_SEED)
        yield

    application = FastAPI(title="Academic Analytics Copilot", version="0.2.0",
                          description="Catalog-grounded academic analytics. DeepSeek is the formal LLM; uploaded data uses local deterministic queries only.",
                          lifespan=lifespan)
    application.state.database = store
    application.add_middleware(UploadBodyLimit, limit=max_request_bytes)
    application.add_middleware(UploadBodyLimit, limit=8192, path="/api/agent/query")
    application.add_middleware(UploadBodyLimit, limit=8192, path="/api/agent/resolve")
    for path in ("/api/datasets/synthetic/preview", "/api/datasets/synthetic/commit"):
        application.add_middleware(UploadBodyLimit, limit=8192, path=path)
    for path in ("/api/datasets/college/preview", "/api/datasets/college/commit"):
        application.add_middleware(UploadBodyLimit, limit=MAX_FILE_BYTES + 65536, path=path)

    @application.exception_handler(AgentError)
    async def agent_error(request: Request, exc: AgentError):
        response = error_response(exc.code, str(exc), exc.status)
        body = {"error": {"code": exc.code, "message": str(exc)}, "request_id": exc.request_id or uuid4().hex,
                "tool_calls": [trace.model_dump(mode="json") for trace in exc.tool_calls],
                "model_trace": [trace.model_dump(mode="json") for trace in exc.model_trace]}
        return JSONResponse(body, status_code=response.status_code)

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
        return {"status": "ok", "dataset_active": store.has_dataset(), "version": "0.2.0"}

    def agent_runtime():
        settings = LLMSettings() if agent_provider is not None else LLMSettings.from_env()
        if agent_provider is None and settings.provider not in {"deepseek", "mock"}:
            raise AgentError("llm_configuration_error", "正式模型仅支持DeepSeek；Mock只供明确配置的离线测试。", 503)
        return agent_provider or build_provider(settings), settings

    @application.get("/api/agent/status")
    def agent_status():
        provider, settings = agent_runtime()
        return {"provider": provider.name, "model": provider.model, "simulated": provider.simulated,
                "tools": list(TOOLS), "max_model_turns": MAX_MODEL_TURNS, "max_tool_calls": MAX_TOOL_CALLS,
                "request_timeout_seconds": agent_timeout_seconds or settings.request_timeout_seconds,
                "question_policy": "catalog_grounded_academic_cn_en", "streaming": False}

    def resolve_payload(payload, *, generated_only=False):
        info = store.overview(expected_version=payload.expected_version, generated_only=generated_only)
        catalog = store.reporting_catalog()
        if catalog.dataset_version != info.dataset_version:
            raise DataError("数据在解析问题时发生变化，请重新确认范围。", code="dataset_changed", status=409)
        intent = resolve_question(payload.question, catalog, payload.scope)
        after = store.overview(expected_version=info.dataset_version, generated_only=generated_only)
        if after.source != info.source:
            raise DataError("数据来源已改变，请重新确认范围。", code="dataset_changed", status=409)
        return intent, info.dataset_version

    @application.post("/api/agent/resolve", response_model=ResolvedIntent)
    async def resolve_agent(payload: QueryRequest):
        intent, _ = await run_in_threadpool(resolve_payload, payload,
                                          generated_only=payload.execution_mode != "local")
        return intent

    @application.post("/api/agent/query", response_model=AgentResponse)
    async def agent_query(payload: QueryRequest):
        if payload.execution_mode == "local":
            # Local deterministic path: no provider is constructed, so an
            # uploaded dataset can be answered without any external request.
            return await AgentService(store, None, timeout_seconds=agent_timeout_seconds or 45.0).query(
                payload.question, scope=payload.scope, expected_version=payload.expected_version,
                execution_mode="local")
        # generated_only=True rejects uploaded provenance before any provider
        # setting is read: a missing key must never mask the privacy decision.
        intent, expected_version = await run_in_threadpool(resolve_payload, payload, generated_only=True)
        # Chinese (or any non-ASCII) prose is compiled locally into a verified
        # ACADEMIC_INTENT before it can reach an external model. It is never
        # rejected merely for containing Chinese characters. `grounded` is the
        # same constrained path, selected explicitly by a caller.
        grounded = payload.execution_mode == "grounded" or not payload.question.isascii()
        needs_model = not grounded or intent.status == "ready"
        if needs_model:
            provider, settings = agent_runtime()
            timeout = agent_timeout_seconds or settings.request_timeout_seconds
        else:
            # Clarification / unsupported notices need no model and no API key.
            provider, timeout = None, agent_timeout_seconds or 45.0
        return await AgentService(store, provider, timeout_seconds=timeout).query(payload.question,
            scope=payload.scope, expected_version=expected_version,
            execution_mode="grounded" if grounded else "configured",
            intent=intent if grounded else None)

    @application.get("/api/dataset", response_model=DatasetInfo)
    def dataset():
        return store.overview()

    @application.get("/api/catalog", response_model=Catalog,
                     response_model_exclude={"classes": {"__all__": {"major"}}, "courses": {"__all__": {"course_type"}}})
    def catalog():
        return store.catalog()

    @application.get("/api/reports/catalog", response_model=ReportingCatalog)
    def reporting_catalog():
        return store.reporting_catalog()

    @application.get("/api/reports/courses", response_model=CourseReport)
    def course_report(parameters: Annotated[CourseReportQuery, Query()]):
        scope = ReportScope(**parameters.model_dump(exclude={"expected_version"}))
        return engine.course_report(scope, expected_version=parameters.expected_version)

    @application.get("/api/analytics/summary", response_model=Statistics)
    def summary(scope: Annotated[Scope, Query()]):
        return engine.summary(scope)

    @application.get("/api/analytics/compare-classes", response_model=ClassComparison)
    def compare_classes(course_id: CourseId, class_ids: Annotated[list[ClassId], Query(min_length=2, max_length=40)],
                        term: Term = "2026-T1"):
        return engine.compare_classes(course_id, class_ids, term)

    async def college_upload(request, workbook, allowed):
        form = await request.form()
        if set(form) - allowed or any(len(form.getlist(k)) != 1 for k in form):
            raise DataError("Unexpected or duplicate multipart fields")
        return await read_upload(workbook)

    @application.post("/api/datasets/college/preview", response_model=CollegePreview)
    async def preview_college(request: Request, workbook: UploadFile = File(), duplicate_policy: DuplicatePolicy = Form("block")):
        try:
            upload = await college_upload(request, workbook, {"workbook", "duplicate_policy"})
            return await run_in_threadpool(college_import.preview, *upload, duplicate_policy=duplicate_policy)
        finally:
            await workbook.close()

    @application.post("/api/datasets/college/commit", response_model=CollegeCommitResult)
    async def commit_college(request: Request, workbook: UploadFile = File(), preview_token: str = Form(),
                             confirmed: bool = Form(False)):
        try:
            upload = await college_upload(request, workbook, {"workbook", "preview_token", "confirmed"})
            return await run_in_threadpool(college_import.commit, *upload, preview_token=preview_token, confirmed=confirmed)
        finally:
            await workbook.close()

    @application.post("/api/datasets/synthetic/preview", response_model=CollegePreview)
    async def preview_synthetic(options: SyntheticJSONOptions):
        return await run_in_threadpool(synthetic_import.preview, options)

    @application.post("/api/datasets/synthetic/commit", response_model=CollegeCommitResult)
    async def commit_synthetic(payload: SyntheticCommitRequest):
        return await run_in_threadpool(synthetic_import.commit, payload.options, payload.preview_token, payload.confirmed)

    @application.get("/api/datasets/synthetic/export")
    async def export_synthetic(expected_version: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")]):
        content = await run_in_threadpool(build_synthetic_export, store, expected_version)
        return Response(content, media_type=XLSX_MEDIA_TYPE,
                        headers={"Content-Disposition": 'attachment; filename="synthetic-grades-snapshot.xlsx"',
                                 "X-Dataset-Version": expected_version,
                                 "Cache-Control": "no-store"})

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


# The staff workflow starts with an upload. Example activation is explicit;
# callers of create_app may still opt into the original seeded test fixture.
app = create_app(bootstrap_demo=False)

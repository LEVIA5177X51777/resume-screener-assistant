"""FastAPI application: REST API and server-rendered pages."""

import base64
import logging
import math
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import ui
from .config import Settings
from .config import settings as default_settings
from .db import Database
from .llm import LLMError, Provider, get_provider
from .schemas import AnalysisList, AnalysisRecord, AnalysisStats, AnalyzeRequest, ErrorResponse
from .service import run_analysis
from .text_utils import InputError, extract_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("screener")

BASE_DIR = Path(__file__).parent
SAMPLE_DIR = BASE_DIR.parent / "sample_data"
templates = Jinja2Templates(directory=BASE_DIR / "templates")

SortOption = Literal["newest", "oldest", "score_desc", "score_asc"]
SampleName = Literal["jd", "strong", "weak", "injection"]
SAMPLES: dict[str, tuple[str, str | None]] = {
    "jd": ("jd_backend_engineer.txt", None),
    "strong": ("resume_strong.txt", "Priya Sharma"),
    "weak": ("resume_weak.txt", "Rahul Verma"),
    "injection": ("resume_injection.txt", "Alex Mercer"),
}


def create_app(
    settings: Settings = default_settings, db: Database | None = None, provider: Provider | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        purged = app.state.db.purge_older_than(settings.retention_days)
        log.info("startup provider=%s model=%s purged_expired=%d", settings.llm_provider, settings.model_name, purged)
        yield

    app = FastAPI(title="Resume Screener Assistant", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db or Database(settings.database_path)
    app.state.provider = provider
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    ui.register(templates.env, settings)

    if settings.basic_auth_user and settings.basic_auth_password:
        expected = (settings.basic_auth_user, settings.basic_auth_password)

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path != "/api/health":
                try:
                    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
                    user, _, password = base64.b64decode(encoded).decode().partition(":")
                    ok = scheme.lower() == "basic" and all(
                        secrets.compare_digest(a.encode(), b.encode())
                        for a, b in zip((user, password), expected, strict=True)
                    )
                except (ValueError, UnicodeDecodeError):
                    ok = False
                if not ok:
                    return JSONResponse(
                        status_code=401,
                        content=ErrorResponse(error="Authentication required").model_dump(),
                        headers={"WWW-Authenticate": 'Basic realm="Resume Screener"'},
                    )
            return await call_next(request)

    def get_db() -> Database:
        return app.state.db

    def get_llm() -> Provider:
        if app.state.provider is None:
            app.state.provider = get_provider(settings)
        return app.state.provider

    @app.exception_handler(InputError)
    async def _input_error(_: Request, exc: InputError):
        return JSONResponse(status_code=422, content=ErrorResponse(error=str(exc)).model_dump())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=ErrorResponse(error=str(exc.detail)).model_dump())

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError):
        log.error("llm failure type=%s detail=%s", type(exc).__name__, exc)
        return JSONResponse(status_code=exc.http_status, content=ErrorResponse(error=exc.user_message).model_dump())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        msgs = []
        for e in exc.errors():
            field = str(e["loc"][-1]).replace("_", " ")
            msgs.append(f"{field}: {'is required' if e['type'] in ('missing', 'string_too_short') else e['msg']}")
        return JSONResponse(
            status_code=422, content=ErrorResponse(error="Invalid input", detail="; ".join(msgs)).model_dump()
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content=ErrorResponse(error="Unexpected server error.").model_dump())

    @app.get("/api/health")
    def health():
        return {"status": "ok", "provider": settings.llm_provider, "model": settings.model_name}

    @app.post(
        "/api/analyses",
        response_model=AnalysisRecord,
        status_code=201,
        responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def create_analysis(req: AnalyzeRequest, db: Database = Depends(get_db), llm: Provider = Depends(get_llm)):
        return run_analysis(req, llm, db, settings)

    @app.get("/api/analyses", response_model=AnalysisList)
    def list_analyses(
        db: Database = Depends(get_db),
        name: Annotated[str | None, Query(max_length=120)] = None,
        min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
        max_score: Annotated[int | None, Query(ge=0, le=100)] = None,
        sort: SortOption = "newest",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if min_score is not None and max_score is not None and min_score > max_score:
            raise InputError("min_score cannot be greater than max_score.")
        items, total = db.list(
            name=name, min_score=min_score, max_score=max_score, sort=sort, limit=limit, offset=offset
        )
        return AnalysisList(items=items, total=total, limit=limit, offset=offset)

    @app.get("/api/analyses/{analysis_id}", response_model=AnalysisRecord, responses={404: {"model": ErrorResponse}})
    def get_analysis(analysis_id: int, db: Database = Depends(get_db)):
        record = db.get(analysis_id)
        if not record:
            raise HTTPException(404, "Analysis not found")
        return record

    @app.delete("/api/analyses/{analysis_id}", status_code=204)
    def delete_analysis(analysis_id: int, db: Database = Depends(get_db)):
        if not db.delete(analysis_id):
            raise HTTPException(404, "Analysis not found")

    @app.get("/api/stats", response_model=AnalysisStats)
    def stats(db: Database = Depends(get_db)):
        return db.stats()

    @app.post("/api/extract-text")
    async def extract(file: UploadFile = File(...)):
        data = await file.read()
        return {"filename": file.filename, "text": extract_text(file.filename or "", data)}

    @app.get("/api/samples/{name}")
    def sample(name: SampleName):
        filename, candidate_name = SAMPLES[name]
        return {"candidate_name": candidate_name, "text": (SAMPLE_DIR / filename).read_text(encoding="utf-8")}

    @app.get("/", include_in_schema=False)
    def index_page(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {"max_chars": settings.max_input_chars, "min_chars": settings.min_input_chars},
        )

    @app.get("/history", include_in_schema=False)
    def history_page(
        request: Request,
        db: Database = Depends(get_db),
        name: str = "",
        min_score: str = "",
        max_score: str = "",
        sort: str = "newest",
        page: int = 1,
    ):
        per_page = 15
        error = None
        lo, hi = _parse_score(min_score), _parse_score(max_score)
        if lo is not None and hi is not None and lo > hi:
            error, lo, hi = "Min score cannot be greater than max score.", None, None
        page = max(page, 1)
        items, total = db.list(
            name=name.strip() or None,
            min_score=lo,
            max_score=hi,
            sort=sort,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        return templates.TemplateResponse(
            request,
            "history.html",
            {
                "items": items,
                "total": total,
                "page": page,
                "pages": max(1, math.ceil(total / per_page)),
                "filters": {"name": name, "min_score": min_score, "max_score": max_score, "sort": sort},
                "error": error,
                "stats": db.stats(),
            },
        )

    @app.get("/analyses/{analysis_id}", include_in_schema=False)
    def detail_page(request: Request, analysis_id: int, db: Database = Depends(get_db)):
        record = db.get(analysis_id)
        if not record:
            return templates.TemplateResponse(request, "not_found.html", status_code=404)
        return templates.TemplateResponse(request, "detail.html", {"r": record})

    return app


def _parse_score(value: str) -> int | None:
    try:
        return min(max(int(value), 0), 100)
    except (TypeError, ValueError):
        return None


app = create_app()

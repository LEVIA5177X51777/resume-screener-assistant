"""FastAPI app: JSON API under /api, server-rendered pages for HR users."""
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, settings as default_settings
from .db import Database
from .llm import LLMError, Provider, get_provider
from .schemas import AnalysisList, AnalysisRecord, AnalyzeRequest, ErrorResponse
from .service import run_analysis
from .text_utils import InputError, extract_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("screener")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")  # autoescaping on: LLM output can't inject HTML

SortOption = Literal["newest", "oldest", "score_desc", "score_asc"]


def create_app(settings: Settings = default_settings, db: Database | None = None,
               provider: Provider | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        purged = app.state.db.purge_older_than(settings.retention_days)
        log.info("startup provider=%s model=%s purged_expired=%d", settings.llm_provider, settings.model_name, purged)
        yield

    app = FastAPI(title="Resume Screener Assistant", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db or Database(settings.database_path)
    app.state.provider = provider  # built lazily so a missing key gives a clean error, not a crash
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    # ------------------------------------------------------------ dependencies

    def get_db() -> Database:
        return app.state.db

    def get_llm() -> Provider:
        if app.state.provider is None:
            app.state.provider = get_provider(settings)
        return app.state.provider

    # ------------------------------------------------------------ error handling

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
        return JSONResponse(status_code=422, content=ErrorResponse(error="Invalid input", detail="; ".join(msgs)).model_dump())

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content=ErrorResponse(error="Unexpected server error.").model_dump())

    # ------------------------------------------------------------ API

    @app.get("/api/health")
    def health():
        return {"status": "ok", "provider": settings.llm_provider, "model": settings.model_name}

    @app.post("/api/analyses", response_model=AnalysisRecord, status_code=201,
              responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
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
        items, total = db.list(name=name, min_score=min_score, max_score=max_score, sort=sort, limit=limit, offset=offset)
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

    @app.post("/api/extract-text")
    async def extract(file: UploadFile = File(...)):
        data = await file.read()
        return {"filename": file.filename, "text": extract_text(file.filename or "", data)}

    # ------------------------------------------------------------ pages

    @app.get("/", include_in_schema=False)
    def index_page(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "provider": settings.llm_provider, "max_chars": settings.max_input_chars,
            "min_chars": settings.min_input_chars,
        })

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
        items, total = db.list(name=name.strip() or None, min_score=lo, max_score=hi, sort=sort,
                               limit=per_page, offset=(page - 1) * per_page)
        return templates.TemplateResponse(request, "history.html", {
            "items": items, "total": total, "page": page, "pages": max(1, math.ceil(total / per_page)),
            "filters": {"name": name, "min_score": min_score, "max_score": max_score, "sort": sort},
            "error": error,
        })

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

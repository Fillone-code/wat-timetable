from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .client import TimetableClient
from .errors import WatplanError
from .models import Period, Schedule, Semester
from .service import TimetableService


class GroupsResponse(BaseModel):
    groups: list[str]


class SemestersResponse(BaseModel):
    semesters: list[Semester]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def create_app(*, html_path: str | Path | None = None, service: TimetableService | None = None) -> FastAPI:
    owned = service is None
    timetable = service or TimetableService(TimetableClient(html_path=html_path or os.environ.get("WATPLAN_HTML")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if owned:
            timetable.close()

    app = FastAPI(
        title="watplan", version="0.1.0", lifespan=lifespan,
        description="Read-only WCY WAT timetable API. Dates and week boundaries use Europe/Warsaw. "
                    "Semester IDs are listed at /semesters; current selects the latest published semester that has started.",
        responses={422: {"model": ErrorResponse}},
    )

    @app.exception_handler(WatplanError)
    async def handle_error(request: Request, exc: WatplanError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        message = "; ".join(f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors())
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": message}})

    @app.get("/health", tags=["status"])
    def health():
        """Process health only; does not contact WAT."""
        return {"status": "ok"}

    @app.get("/groups", response_model=GroupsResponse, tags=["catalog"], responses={502: {"model": ErrorResponse}})
    def groups(group: str = Query(..., description="WAT group name"), search: str = "", refresh: bool = False):
        catalog = timetable.catalog(group, refresh=refresh)
        return {"groups": [g for g in catalog.groups if search.casefold() in g.casefold()]}

    @app.get("/semesters", response_model=SemestersResponse, tags=["catalog"], responses={502: {"model": ErrorResponse}})
    def semesters(group: str = Query(..., description="WAT group name"), refresh: bool = False):
        return {"semesters": timetable.catalog(group, refresh=refresh).semesters}

    @app.get("/plan", response_model=Schedule, tags=["timetable"], responses={
        400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 502: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    })
    def plan(
        group: str = Query(..., description="WAT group name"),
        semester: str = Query("current", description="current, winter-2025, or 2025-2026-winter"),
        period: Period = "this-week",
        on: date | None = Query(None, alias="date", description="Required for day/week; YYYY-MM-DD"),
        start: date | None = Query(None, description="Inclusive range start"),
        end: date | None = Query(None, description="Inclusive range end"),
        refresh: bool = False,
    ):
        """Return lessons grouped by date, including ISO timestamps with UTC offsets.

        Week periods run Monday–Sunday. Use period=semester for every lesson
        in a term, or period=range with start/end for a custom inclusive range.
        Empty periods return 200 with days=[] and lesson_count=0.
        """
        return timetable.get_schedule(group, semester=semester, period=period, on=on, start=start, end=end, refresh=refresh)

    return app

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

WARSAW = ZoneInfo("Europe/Warsaw")
Period = Literal["today", "tomorrow", "day", "this-week", "next-week", "week", "semester", "range"]


class Semester(BaseModel):
    id: str
    label: str
    kind: Literal["winter", "summer", "resit"]
    academic_year: str
    start_date: date
    source_timestamp: int


class Lesson(BaseModel):
    date: date
    block: int
    start_time: str | None = None
    end_time: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    subject: str
    subject_short: str
    lesson_type: str
    lesson_type_short: str
    room: str | None = None
    instructors: list[str] = Field(default_factory=list)
    occurrence: int | None = None


class Day(BaseModel):
    date: date
    weekday: str
    lessons: list[Lesson]


class DateWindow(BaseModel):
    start: date | None
    end: date | None


class SourceInfo(BaseModel):
    url: str
    fetched_at: datetime
    updated_at: str | None
    mode: Literal["live", "html"]


class Schedule(BaseModel):
    schema_version: str = "1.0"
    group: str
    semester: Semester
    timezone: str = "Europe/Warsaw"
    period: Period
    date_range: DateWindow
    available_dates: DateWindow
    lesson_count: int
    days: list[Day]
    source: SourceInfo
    warnings: list[str] = Field(default_factory=list)


class Catalog(BaseModel):
    groups: list[str]
    semesters: list[Semester]


class ParsedPage(BaseModel):
    group: str | None
    groups: list[str]
    semesters: list[Semester]
    selected_semester_id: str | None = None
    lessons: list[Lesson]
    updated_at: str | None
    warnings: list[str] = Field(default_factory=list)
    recognized: bool


from __future__ import annotations

import difflib
import re
from datetime import date, datetime, timedelta

from .client import TimetableClient, source_url
from .errors import InvalidRequest, ParseError, UnknownGroup, UnknownSemester
from .models import Catalog, DateWindow, Day, ParsedPage, Period, Schedule, Semester, SourceInfo, WARSAW
from .parser import parse_page

PERIODS = ("today", "tomorrow", "day", "this-week", "next-week", "week", "semester", "range")


def validate_group(group: str) -> str:
    group = group.strip()
    if not re.fullmatch(r"[A-Za-z]{2,}\d{2}[A-Za-z0-9*_-]{1,40}", group):
        raise InvalidRequest("Use a valid WAT group name.")
    return group


def date_window(period: str, *, on: date | None = None, start: date | None = None,
                end: date | None = None, today: date | None = None) -> DateWindow:
    today = today or datetime.now(WARSAW).date()
    if period not in PERIODS:
        raise InvalidRequest(f"Unknown period {period!r}. Choose: {', '.join(PERIODS)}.")
    if period in ("day", "week") and on is None:
        raise InvalidRequest(f"The {period} period requires a date (YYYY-MM-DD).")
    if on is not None and period not in ("day", "week"):
        raise InvalidRequest("A date is only valid with the day or week period.")
    if period != "range" and (start is not None or end is not None):
        raise InvalidRequest("Start and end are only valid with the range period.")
    if period == "range":
        if start is None or end is None:
            raise InvalidRequest("The range period requires both start and end dates.")
        if end < start:
            raise InvalidRequest("End date must be on or after start date.")
        return DateWindow(start=start, end=end)
    if period == "semester":
        return DateWindow(start=None, end=None)
    if period in ("today", "tomorrow", "day"):
        selected = on if period == "day" else today + timedelta(days=period == "tomorrow")
        return DateWindow(start=selected, end=selected)
    anchor = on if period == "week" else today
    monday = anchor - timedelta(days=anchor.weekday())
    if period == "next-week":
        monday += timedelta(days=7)
    return DateWindow(start=monday, end=monday + timedelta(days=6))


def choose_semester(semesters: list[Semester], selector: str, today: date) -> Semester:
    if not semesters:
        raise ParseError("No semester links were found in the source page.")
    if selector == "current":
        eligible = [s for s in semesters if s.start_date <= today]
        if not eligible:
            raise UnknownSemester("The source has no semester starting on or before today. Choose a semester explicitly.")
        return max(eligible, key=lambda s: s.start_date)
    selector = selector.lower().strip().replace("/", "-")
    for semester in semesters:
        year = semester.academic_year.split("/")[0]
        if selector in (semester.id, f"{semester.kind}-{year}", str(semester.source_timestamp)):
            return semester
    choices = ", ".join(s.id for s in semesters)
    raise UnknownSemester(f"Unknown semester {selector!r}. Available: {choices}.")


def content_semester(page: ParsedPage) -> Semester | None:
    if page.selected_semester_id:
        return next((semester for semester in page.semesters if semester.id == page.selected_semester_id), None)
    if not page.lessons:
        return None
    # Older/simplified snapshots may lack a selected-calendar marker. Infer
    # only when every lesson points to the same term, never from the oldest.
    inferred = set()
    for lesson in page.lessons:
        eligible = [semester for semester in page.semesters if semester.start_date <= lesson.date]
        if eligible:
            inferred.add(max(eligible, key=lambda semester: semester.start_date).id)
    if len(inferred) > 1:
        raise ParseError("The source contains lessons from multiple semesters without a selected calendar.")
    return next((semester for semester in page.semesters if semester.id in inferred), None)


class TimetableService:
    def __init__(self, client: TimetableClient | None = None):
        self.client = client or TimetableClient()

    def close(self) -> None:
        self.client.close()

    def catalog(self, group: str, *, refresh: bool = False) -> Catalog:
        source = self.client.fetch(validate_group(group), refresh=refresh)
        page = parse_page(source.html)
        return Catalog(groups=page.groups, semesters=page.semesters)

    def get_schedule(self, group: str, *, semester: str = "current", period: Period = "this-week",
                     on: date | None = None, start: date | None = None, end: date | None = None,
                     refresh: bool = False, today: date | None = None) -> Schedule:
        group = validate_group(group)
        today = today or datetime.now(WARSAW).date()
        window = date_window(period, on=on, start=start, end=end, today=today)
        source = self.client.fetch(group, refresh=refresh)
        page = parse_page(source.html)
        names = {name.casefold(): name for name in page.groups}
        canonical = names.get(group.casefold())
        if page.groups and canonical is None:
            suggestions = difflib.get_close_matches(group.upper(), page.groups, n=3, cutoff=0.5)
            hint = f" Similar groups: {', '.join(suggestions)}." if suggestions else ""
            raise UnknownGroup(f"Group {group!r} is not listed by WAT.{hint}")
        canonical = canonical or group
        if source.mode == "html":
            if not page.group or page.group.casefold() != canonical.casefold():
                raise UnknownGroup(f"Saved HTML is for {page.group or 'an unknown group'}, not {group}.")
            inferred = content_semester(page)
            if inferred is None and semester == "current":
                raise InvalidRequest("The saved page has no dated lessons, so its semester cannot be inferred. Pass --semester explicitly.")
            selected = inferred if semester == "current" and inferred else choose_semester(page.semesters, semester, today)
            if inferred and inferred.id != selected.id:
                raise InvalidRequest(f"Saved HTML contains {inferred.id}, not {selected.id}. Save that semester's page first.")
        else:
            selected = choose_semester(page.semesters, semester, today)
            # The default page often already displays the requested term.
            # Reuse it instead of making a redundant request with ?date=.
            if page.selected_semester_id != selected.id or page.group != canonical:
                source = self.client.fetch(canonical, selected.source_timestamp, refresh=refresh)
                page = parse_page(source.html)
            if not page.group or page.group.casefold() != canonical.casefold():
                raise UnknownGroup(f"WAT returned a timetable for {page.group or 'an unknown group'}, not {canonical}.")
            inferred = content_semester(page)
            if inferred and inferred.id != selected.id:
                raise ParseError(f"WAT returned lessons for {inferred.id} when {selected.id} was requested.")

        warnings = list(page.warnings)
        if source.mode == "html":
            warnings.append("Loaded from saved HTML; this is a snapshot and is not automatically updated.")
        available = DateWindow(
            start=min((lesson.date for lesson in page.lessons), default=None),
            end=max((lesson.date for lesson in page.lessons), default=None),
        )
        lessons = [lesson for lesson in page.lessons
                   if (window.start is None or lesson.date >= window.start)
                   and (window.end is None or lesson.date <= window.end)]
        if not lessons and page.lessons:
            warnings.append(f"No lessons in this period. The source has lessons from {available.start} through {available.end}.")
        if not page.lessons:
            warnings.append("WAT lists no lessons for this group and semester.")
        grouped: dict[date, list] = {}
        for lesson in lessons:
            grouped.setdefault(lesson.date, []).append(lesson)
        return Schedule(
            group=canonical, semester=selected, period=period, date_range=window,
            available_dates=available, lesson_count=len(lessons),
            days=[Day(date=day, weekday=day.strftime("%A"), lessons=entries) for day, entries in grouped.items()],
            source=SourceInfo(
                url=source_url(canonical, selected.source_timestamp),
                fetched_at=source.fetched_at, updated_at=page.updated_at, mode=source.mode,
            ),
            warnings=warnings,
        )

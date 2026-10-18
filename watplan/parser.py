"""Parse the server-rendered calendar, including its hidden lesson details.

The hidden lesson store spans several terms. Only records whose dates appear
in the selected calendar's .day cells belong to the displayed timetable.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .errors import ParseError, SourceError
from .models import Lesson, ParsedPage, Semester, WARSAW

EVENT = re.compile(r"\b(\d{4}[_-]\d{2}[_-]\d{2})\s+block\s*(\d+)\b", re.I)
DETAIL = re.compile(
    r"^\s*(?P<short>.*?)\s*\((?P<short_type>[^)]+)\)\s*"
    r"(?P<location>.*?)\[\s*(?P<occurrence>\d+)\s*\]\s*"
    r"(?P<subject>.*?)\s+-\s+\((?P<type>[^)]+)\)\s*-\s*"
    r"(?P<instructor>.*?)\s*#(?P<color>[\da-f]{6})(?P<tail>.*)$",
    re.I | re.S,
)
SEMESTER = re.compile(r"s\.\s*(zimowy|letni|popr\.?)\s+(\d{4})\s*/\s*(\d{4})", re.I)
KINDS = {"zimowy": "winter", "letni": "summer", "popr": "resit"}
# Verified against the calendar's block legend. A page legend overrides these.
BLOCK_TIMES = {
    1: ("08:00", "09:35"),
    2: ("09:50", "11:25"),
    3: ("11:40", "13:15"),
    4: ("13:30", "15:05"),
    5: ("16:00", "17:35"),
    6: ("17:50", "19:25"),
    7: ("19:40", "21:15"),
}


def clean(value: str) -> str:
    return " ".join(value.split())


def parse_page(html: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    if re.search(r"access denied|request unsuccessful|incapsula incident|blocked by our security", text, re.I):
        raise SourceError("WAT's security service blocked this request. Use --html with a saved timetable page.")

    groups = []
    for option in soup.select("select option[value]"):
        value = option.get("value", "").strip()
        label = option.get_text(strip=True)
        if re.fullmatch(r"[A-Za-z]{2,}\d{2}[A-Za-z0-9*_-]+", value):
            groups.append(value)
        elif re.fullmatch(r"[A-Za-z]{2,}\d{2}[A-Za-z0-9*_-]+", label):
            groups.append(label)

    semesters = {}
    for link in soup.select("a[href]"):
        match = SEMESTER.search(clean(link.get_text(" ", strip=True)))
        if not match:
            continue
        query = parse_qs(urlsplit(link["href"]).query)
        raw = query.get("date", [""])[0]
        if not raw.isdigit():
            continue
        try:
            timestamp = int(raw)
            start = datetime.fromtimestamp(timestamp, WARSAW).date()
        except (ValueError, OverflowError, OSError):
            continue
        kind = KINDS[match[1].lower().rstrip(".")]
        year, next_year = int(match[2]), int(match[3])
        if next_year != year + 1:
            continue
        semester = Semester(
            id=f"{year}-{next_year}-{kind}", label=clean(link.get_text(" ", strip=True)),
            kind=kind, academic_year=f"{year}/{next_year}", start_date=start,
            source_timestamp=timestamp,
        )
        semesters[semester.id] = semester

    # WAT marks the selected term on the container around its semester links.
    # Each link has a d<timestamp> class too, so inspect only the container.
    by_timestamp = {semester.source_timestamp: semester for semester in semesters.values()}
    selected_ids = set()
    for container in soup.find_all("div", class_=True):
        if not container.select_one("a.date_link"):
            continue
        for class_name in container.get("class", []):
            match = re.fullmatch(r"d(\d+)", class_name)
            if match and int(match[1]) in by_timestamp:
                selected_ids.add(by_timestamp[int(match[1])].id)
    if len(selected_ids) > 1:
        raise ParseError("The source contains conflicting selected-semester markers.")
    selected_id = next(iter(selected_ids), None)

    # The page's JavaScript copies hidden records into matching calendar cells.
    # Filtering by the actual grid dates reproduces that selection, including
    # empty terms, without inferring a term from the oldest hidden lesson.
    calendar = soup.select_one(".rozklad_container")
    calendar_dates = set()
    if calendar is not None:
        for cell in calendar.select(".day"):
            for class_name in cell.get("class", []):
                if re.fullmatch(r"\d{4}_\d{2}_\d{2}", class_name):
                    try:
                        calendar_dates.add(date.fromisoformat(class_name.replace("_", "-")))
                    except ValueError as exc:
                        raise ParseError(f"Invalid calendar date: {class_name}") from exc
        if not calendar_dates:
            raise ParseError("The timetable calendar has no recognizable date cells.")

    group_match = re.search(r"Rozkład\s+zajęć\s+grupy\s*:\s*([^\s]+)", text, re.I)
    group = group_match[1] if group_match else None
    updated = re.search(r"Data\s+aktualizacji\s*:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text, re.I)

    times = dict(BLOCK_TIMES)
    for block, start, end in re.findall(r"(?:^|\n)\s*([1-9])\s+(\d{2}:\d{2})\s+(\d{2}:\d{2})\b", text):
        if start < end and all(int(v.split(":")[0]) < 24 and int(v.split(":")[1]) < 60 for v in (start, end)):
            times[int(block)] = start, end

    markers = list(EVENT.finditer(text))
    if selected_id and calendar is None and markers:
        raise ParseError("The selected semester has no calendar date grid; hidden lessons cannot be selected safely.")
    lessons: dict[tuple, Lesson] = {}
    for index, marker in enumerate(markers):
        try:
            day = date.fromisoformat(marker[1].replace("_", "-"))
        except ValueError as exc:
            raise ParseError(f"Invalid lesson date: {marker[1]}") from exc
        if calendar is not None and day not in calendar_dates:
            continue
        stop = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        chunk = text[marker.end():stop]
        detail = DETAIL.match(chunk)
        if not detail:
            raise ParseError(f"Could not read lesson details for {marker[1]}, block {marker[2]}. The source layout may have changed.")
        values = {k: clean(v) for k, v in detail.groupdict().items()}
        location = values["location"]
        # The instructor abbreviation appears both before [occurrence] and
        # after the tooltip colour. Remove only an exactly matching suffix.
        tail = values["tail"].split()
        if tail and location.endswith(tail[0]):
            location = location[:-len(tail[0])].strip()
        instructor = values["instructor"].strip(" -")
        block = int(marker[2])
        start, end = times.get(block, (None, None))
        lesson = Lesson(
            date=day, block=block, start_time=start, end_time=end,
            starts_at=datetime.combine(day, time.fromisoformat(start), WARSAW) if start else None,
            ends_at=datetime.combine(day, time.fromisoformat(end), WARSAW) if end else None,
            subject=values["subject"], subject_short=values["short"],
            lesson_type=values["type"], lesson_type_short=values["short_type"],
            room=location if location and location != "-" else None,
            instructors=[instructor] if instructor else [], occurrence=int(values["occurrence"]),
        )
        # One event may be listed once per lecturer. Merge only exact lesson
        # identities; concurrent classes in different rooms remain separate.
        key = (day, block, lesson.subject, lesson.subject_short, lesson.lesson_type,
               lesson.lesson_type_short, lesson.room, lesson.occurrence)
        if key in lessons:
            for name in lesson.instructors:
                if name not in lessons[key].instructors:
                    lessons[key].instructors.append(name)
        else:
            lessons[key] = lesson

    recognized = bool(group and semesters)
    if not recognized:
        raise ParseError("This is not a recognizable WAT group timetable (missing group heading or semester links).")
    if not markers and not calendar_dates and not re.search(r"nr\.?\s*bloku", text, re.I) and not (selected_id and soup.select_one(".empty-plan")):
        raise ParseError("No lessons or calendar legend were found. The source layout may have changed.")
    warnings = []
    unknown = sorted({lesson.block for lesson in lessons.values() if lesson.start_time is None})
    if unknown:
        warnings.append(f"No time legend for blocks {', '.join(map(str, unknown))}; their times are null.")
    return ParsedPage(
        group=group, groups=sorted(set(groups)),
        semesters=sorted(semesters.values(), key=lambda s: (s.start_date, s.id)),
        selected_semester_id=selected_id,
        lessons=sorted(lessons.values(), key=lambda e: (e.date, e.block, e.subject, e.room or "")),
        updated_at=clean(updated[1]) if updated else None, warnings=warnings, recognized=recognized,
    )

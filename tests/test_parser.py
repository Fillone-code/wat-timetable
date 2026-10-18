from datetime import timedelta

import pytest

from watplan.errors import ParseError, SourceError
from watplan.parser import parse_page


def test_metadata_and_catalog(html):
    page = parse_page(html)
    assert page.group == "DEMO25CS1"
    assert "DEMO24X*DBa" in page.groups
    assert page.updated_at == "2026-10-18 00:36:38"
    assert page.semesters[0].id == "2025-2026-winter"
    assert page.semesters[0].start_date.isoformat() == "2025-09-29"
    assert page.semesters[2].kind == "resit"
    assert len(page.lessons) == 5


def test_full_fields_and_unicode(html):
    lesson = parse_page(html).lessons[1]
    assert lesson.subject == "Database design"
    assert lesson.subject_short == "DB"
    assert lesson.lesson_type == "Wykład"
    assert lesson.room == "Room A-204"
    assert lesson.instructors == ["Casey Example"]
    assert lesson.occurrence == 2
    assert (lesson.start_time, lesson.end_time) == ("13:30", "15:05")


def test_missing_instructors_and_rooms(html):
    lessons = parse_page(html).lessons
    assert lessons[2].room is None
    assert lessons[2].instructors == []
    assert lessons[3].room == "Room B-16, Room B-17,"
    assert lessons[4].room == "Room C-12"
    assert lessons[4].instructors == []


def test_dst_offsets_follow_lesson_date(html):
    lessons = parse_page(html).lessons
    assert lessons[0].starts_at.utcoffset() == timedelta(hours=2)
    assert lessons[-1].starts_at.utcoffset() == timedelta(hours=1)


def test_legend_overrides_default_time(html):
    changed = html.replace("08:00", "07:45").replace("09:35", "09:20")
    lesson = parse_page(changed).lessons[0]
    assert (lesson.start_time, lesson.end_time) == ("07:45", "09:20")


def test_shared_lecturers_merge_but_different_rooms_do_not(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    event = str(soup.section.div)
    shared = event.replace("Jordan Example", "Taylor Example").replace("JM", "TE")
    other_room = event.replace("Lab A-101", "Lab A-102")
    page = parse_page(html.replace("</section>", shared + shared + other_room + "</section>"))
    assert len(page.lessons) == 6
    assert page.lessons[0].instructors == ["Jordan Example", "Sam Example", "Taylor Example"]
    assert page.lessons[1].room == "Lab A-102"


def test_unknown_block_does_not_invent_times(html):
    page = parse_page(html.replace("block1", "block9"))
    lesson = next(lesson for lesson in page.lessons if lesson.block == 9)
    assert lesson.start_time is None
    assert lesson.starts_at is None
    assert "blocks 9" in page.warnings[0]


@pytest.mark.parametrize("content", ["<h1>Maintenance</h1>", "", "<html>Not a timetable</html>"])
def test_unrecognized_html_is_an_error(content):
    with pytest.raises(ParseError):
        parse_page(content)


def test_success_status_block_page_is_an_error():
    with pytest.raises(SourceError, match="blocked"):
        parse_page("<html>Access denied. This request was blocked by our security service</html>")


def test_broken_lesson_is_not_silently_skipped(html):
    with pytest.raises(ParseError, match="lesson details"):
        parse_page(html.replace("[3]Introduction to programming", "Missing details"))


def test_empty_calendar_and_disappearing_layout_are_distinct(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    soup.section.decompose()
    assert parse_page(str(soup)).lessons == []
    soup.table.decompose()
    with pytest.raises(ParseError, match="calendar legend"):
        parse_page(str(soup))

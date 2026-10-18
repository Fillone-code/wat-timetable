from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from watplan.client import TimetableClient
from watplan.errors import InvalidRequest, ParseError
from watplan.parser import parse_page
from watplan.service import TimetableService, content_semester


@pytest.fixture(scope="module")
def synthetic_calendar():
    return (Path(__file__).parent / "fixtures" / "multi-semester.html").read_text(encoding="utf-8")


def select_calendar(html, timestamp, start, end):
    soup = BeautifulSoup(html, "html.parser")
    soup.find("div", class_="d1790546400")["class"] = [f"d{timestamp}"]
    calendar = soup.select_one(".rozklad_container")
    calendar.clear()
    while start <= end:
        calendar.append(soup.new_tag("div", attrs={"class": ["day", start.strftime("%Y_%m_%d")]}))
        start += timedelta(days=1)
    return str(soup)


@pytest.fixture(scope="module")
def summer_variant(synthetic_calendar):
    return select_calendar(synthetic_calendar, 1771801200, date(2026, 2, 23), date(2026, 6, 28))


def test_synthetic_grid_selects_only_current_calendar(synthetic_calendar):
    page = parse_page(synthetic_calendar)
    assert page.selected_semester_id == "2026-2027-winter"
    assert content_semester(page).id == "2026-2027-winter"
    assert len(page.lessons) == 6
    assert page.lessons[0].date == date(2026, 10, 1)
    assert page.lessons[-1].date == date(2026, 10, 23)
    assert page.group == "DEMO25CS1"


def test_current_and_next_week_from_synthetic_source(synthetic_calendar):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=synthetic_calendar)

    service = TimetableService(TimetableClient(transport=httpx.MockTransport(handler)))
    try:
        this_week = service.get_schedule("DEMO25CS1", today=date(2026, 10, 18))
        assert this_week.semester.id == "2026-2027-winter"
        assert this_week.lesson_count == 0
        assert this_week.date_range.start == date(2026, 10, 12)
        assert this_week.date_range.end == date(2026, 10, 18)
        assert "date=1790546400" in this_week.source.url
        next_week = service.get_schedule("DEMO25CS1", period="next-week", today=date(2026, 10, 18))
        assert next_week.lesson_count == 5
        assert next_week.days[0].date == date(2026, 10, 19)
        assert next_week.days[-1].date == date(2026, 10, 23)
        assert len(requests) == 1
    finally:
        service.close()


def test_summer_grid_does_not_leak_later_lessons(synthetic_calendar, summer_variant):
    requests = []

    def handler(request):
        requests.append(request)
        html = summer_variant if request.url.params.get("date") == "1771801200" else synthetic_calendar
        return httpx.Response(200, text=html)

    service = TimetableService(TimetableClient(transport=httpx.MockTransport(handler)))
    try:
        plan = service.get_schedule("DEMO25CS1", semester="2025-2026-summer", today=date(2026, 10, 18))
        assert plan.semester.id == "2025-2026-summer"
        assert plan.lesson_count == 0
        assert plan.available_dates.start == date(2026, 3, 2)
        assert plan.available_dates.end == date(2026, 6, 12)
        assert "date=1771801200" in plan.source.url
        full = service.get_schedule("DEMO25CS1", semester="summer-2025", period="semester")
        assert full.lesson_count == 2
        assert len(requests) == 2
    finally:
        service.close()


def test_server_returning_wrong_calendar_still_fails(synthetic_calendar):
    service = TimetableService(TimetableClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=synthetic_calendar)
    )))
    try:
        with pytest.raises(ParseError, match="2026-2027-winter when 2025-2026-summer was requested"):
            service.get_schedule("DEMO25CS1", semester="summer-2025")
    finally:
        service.close()


def test_empty_selected_calendar_is_not_filled_from_hidden_lessons(synthetic_calendar, tmp_path):
    empty = select_calendar(synthetic_calendar, 1759096800, date(2025, 9, 29), date(2026, 2, 22))
    path = tmp_path / "empty-winter.html"
    path.write_text(empty, encoding="utf-8")
    service = TimetableService(TimetableClient(html_path=path))
    try:
        plan = service.get_schedule("DEMO25CS1", period="semester")
        assert plan.semester.id == "2025-2026-winter"
        assert plan.lesson_count == 0
        assert plan.days == []
        assert plan.available_dates.start is None
    finally:
        service.close()


def test_snapshot_only_serves_its_selected_calendar(synthetic_calendar, tmp_path):
    path = tmp_path / "winter.html"
    path.write_text(synthetic_calendar, encoding="utf-8")
    service = TimetableService(TimetableClient(html_path=path))
    try:
        with pytest.raises(InvalidRequest, match="Saved HTML contains 2026-2027-winter"):
            service.get_schedule("DEMO25CS1", semester="summer-2025")
    finally:
        service.close()


def test_missing_selected_grid_fails_instead_of_mixing_terms(synthetic_calendar):
    soup = BeautifulSoup(synthetic_calendar, "html.parser")
    soup.select_one(".rozklad_container").decompose()
    with pytest.raises(ParseError, match="no calendar date grid"):
        parse_page(str(soup))


def test_mixed_terms_without_calendar_metadata_are_not_guessed(synthetic_calendar):
    soup = BeautifulSoup(synthetic_calendar, "html.parser")
    soup.select_one(".rozklad_container").decompose()
    soup.find("div", class_="d1790546400")["class"] = []
    with pytest.raises(ParseError, match="multiple semesters"):
        content_semester(parse_page(str(soup)))


def test_cli_json_uses_synthetic_fixture(capsys):
    import json
    from watplan.cli import main

    path = Path(__file__).parent / "fixtures" / "multi-semester.html"
    assert main(["plan", "DEMO25CS1", "--html", str(path), "--period", "week",
                 "--date", "2026-10-21", "--json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["semester"]["id"] == "2026-2027-winter"
    assert payload["lesson_count"] == 5
    assert "date=1790546400" in payload["source"]["url"]
    assert output.err == ""

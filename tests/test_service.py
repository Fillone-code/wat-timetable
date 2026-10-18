from datetime import date

import httpx
import pytest

from watplan.client import TimetableClient
from watplan.errors import InvalidRequest, ParseError, SourceError, UnknownGroup, UnknownSemester
from watplan.service import TimetableService, date_window


@pytest.mark.parametrize("period,on,start,end,expected", [
    ("this-week", None, None, None, ("2026-10-12", "2026-10-18")),
    ("next-week", None, None, None, ("2026-10-19", "2026-10-25")),
    ("today", None, None, None, ("2026-10-18", "2026-10-18")),
    ("tomorrow", None, None, None, ("2026-10-19", "2026-10-19")),
    ("day", date(2025, 10, 20), None, None, ("2025-10-20", "2025-10-20")),
    ("week", date(2026, 1, 1), None, None, ("2025-12-29", "2026-01-04")),
    ("range", None, date(2025, 10, 20), date(2025, 10, 22), ("2025-10-20", "2025-10-22")),
])
def test_calendar_windows(period, on, start, end, expected):
    window = date_window(period, on=on, start=start, end=end, today=date(2026, 10, 18))
    assert (window.start.isoformat(), window.end.isoformat()) == expected


@pytest.mark.parametrize("kwargs", [
    {"period": "day"}, {"period": "week"}, {"period": "other"},
    {"period": "range", "start": date(2025, 10, 22)},
    {"period": "range", "start": date(2025, 10, 22), "end": date(2025, 10, 20)},
    {"period": "today", "on": date(2025, 10, 20)},
    {"period": "semester", "start": date(2025, 10, 20)},
])
def test_invalid_windows(kwargs):
    with pytest.raises(InvalidRequest):
        date_window(**kwargs)


def test_snapshot_filters_and_case_insensitive_group(html_path):
    service = TimetableService(TimetableClient(html_path=html_path))
    try:
        plan = service.get_schedule("demo25cs1", period="week", on=date(2025, 10, 22))
        assert plan.group == "DEMO25CS1"
        assert plan.semester.id == "2025-2026-winter"
        assert plan.lesson_count == 4
        assert [day.date.isoformat() for day in plan.days] == ["2025-10-20", "2025-10-22"]
        assert plan.source.mode == "html"
        assert "date=1759096800" in plan.source.url
        assert "snapshot" in plan.warnings[0]
        assert plan.model_dump(mode="json")["days"][0]["lessons"][0]["starts_at"].endswith("+02:00")
    finally:
        service.close()


def test_snapshot_wrong_semester_and_group(html_path):
    service = TimetableService(TimetableClient(html_path=html_path))
    try:
        with pytest.raises(InvalidRequest, match="Saved HTML contains"):
            service.get_schedule("DEMO25CS1", semester="summer-2025")
        with pytest.raises(UnknownGroup, match="Saved HTML is for"):
            service.get_schedule("DEMO25CS2")
        with pytest.raises(UnknownGroup, match="Similar groups"):
            service.get_schedule("DEMO25CS9")
        with pytest.raises(UnknownSemester):
            service.get_schedule("DEMO25CS1", semester="winter-1999")
    finally:
        service.close()


def test_empty_result_explains_available_dates(html_path):
    service = TimetableService(TimetableClient(html_path=html_path))
    try:
        plan = service.get_schedule("DEMO25CS1", period="day", on=date(2025, 10, 21))
        assert plan.lesson_count == 0
        assert plan.days == []
        assert "2025-10-20" in plan.warnings[-1]
    finally:
        service.close()


def test_empty_snapshot_requires_explicit_semester(html, tmp_path):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    soup.section.decompose()
    saved = tmp_path / "empty.html"
    saved.write_text(str(soup), encoding="utf-8")
    service = TimetableService(TimetableClient(html_path=saved))
    try:
        with pytest.raises(InvalidRequest, match="cannot be inferred"):
            service.get_schedule("DEMO25CS1")
        plan = service.get_schedule("DEMO25CS1", semester="winter-2025", period="semester")
        assert plan.lesson_count == 0
        assert plan.available_dates.start is None
        assert "WAT lists no lessons" in plan.warnings[-1]
    finally:
        service.close()


def test_live_uses_discovered_timestamp_and_cache(html):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=html)

    service = TimetableService(TimetableClient(transport=httpx.MockTransport(handler)))
    try:
        kwargs = dict(semester="winter-2025", period="semester")
        plan = service.get_schedule("DEMO25CS1", **kwargs)
        assert len(requests) == 2
        assert requests[1].url.params["date"] == "1759096800"
        assert plan.lesson_count == 5
        assert plan.source.mode == "live"
        service.get_schedule("DEMO25CS1", **kwargs)
        assert len(requests) == 2
        service.get_schedule("DEMO25CS1", refresh=True, **kwargs)
        assert len(requests) == 4
    finally:
        service.close()


def test_source_ignoring_semester_is_an_error(html):
    client = TimetableClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html)))
    service = TimetableService(client)
    try:
        with pytest.raises(ParseError, match="when 2026-2027-winter was requested"):
            service.get_schedule("DEMO25CS1", today=date(2026, 10, 18))
    finally:
        service.close()


@pytest.mark.parametrize("status", [403, 429, 500])
def test_http_failures_are_actionable(status):
    client = TimetableClient(transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        with pytest.raises(SourceError, match=str(status)):
            client.fetch("DEMO25CS1")
    finally:
        client.close()


def test_missing_html_file(tmp_path):
    client = TimetableClient(html_path=tmp_path / "missing.html")
    try:
        with pytest.raises(SourceError, match="Could not read saved HTML"):
            client.fetch("DEMO25CS1")
    finally:
        client.close()


def test_cross_origin_redirect_is_rejected():
    client = TimetableClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"location": "https://example.com/"})
    ))
    try:
        with pytest.raises(SourceError, match="outside its HTTPS origin"):
            client.fetch("DEMO25CS1")
    finally:
        client.close()

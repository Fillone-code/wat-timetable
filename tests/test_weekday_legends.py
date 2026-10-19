from datetime import date, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from watplan.api import create_app
from watplan.parser import parse_page


FIXTURE = Path(__file__).parent / "fixtures" / "weekday-legends.html"
WEEKDAY = [("16:00", "17:35"), ("17:50", "19:25"), ("19:40", "21:15")]
WEEKEND = [("15:20", "16:55"), ("17:10", "18:45"), ("19:00", "20:35")]


@pytest.mark.parametrize("offset", range(7))
@pytest.mark.parametrize("block", [5, 6, 7])
def test_lesson_uses_its_own_calendar_column(offset, block):
    page = parse_page(FIXTURE.read_text())
    day = date(2026, 10, 19) + timedelta(days=offset)
    lesson = next(item for item in page.lessons if item.date == day and item.block == block)
    start, end = (WEEKDAY if offset < 5 else WEEKEND)[block - 5]
    assert (lesson.start_time, lesson.end_time) == (start, end)
    assert lesson.starts_at.strftime("%H:%M") == start
    assert lesson.ends_at.strftime("%H:%M") == end
    # The Sunday fixture crosses the Warsaw daylight-saving boundary.
    assert lesson.starts_at.utcoffset() == timedelta(hours=1 if offset == 6 else 2)


def test_column_order_does_not_change_lesson_times():
    html = FIXTURE.read_text()
    soup = BeautifulSoup(html, "html.parser")
    calendar = soup.select_one(".rozklad_container")
    for column in list(calendar.select(".day_v1")):
        calendar.insert(0, column.extract())
    assert parse_page(str(soup)).lessons == parse_page(html).lessons


def test_day_specific_legend_overrides_only_its_own_dates():
    soup = BeautifulSoup(FIXTURE.read_text(), "html.parser")
    soup.select_one(".wt .block5 .hr1").string = "16:10"
    soup.select_one(".wt .block5 .hr2").string = "17:45"
    page = parse_page(str(soup))
    starts = {item.date.weekday(): item.start_time for item in page.lessons if item.block == 5}
    assert starts == {0: "16:00", 1: "16:10", 2: "16:00", 3: "16:00", 4: "16:00", 5: "15:20", 6: "15:20"}


def test_missing_column_legend_does_not_inherit_sundays_times():
    soup = BeautifulSoup(FIXTURE.read_text(), "html.parser")
    soup.select_one(".pon .day_blocks").decompose()
    monday = [item for item in parse_page(str(soup)).lessons if item.date.weekday() == 0]
    assert [(item.start_time, item.end_time) for item in monday] == WEEKDAY


def test_api_json_has_matching_clock_times_and_timestamps():
    with TestClient(create_app(html_path=FIXTURE)) as client:
        response = client.get("/plan", params={"group": "DEMO25CS1", "period": "week", "date": "2026-10-19"})
    assert response.status_code == 200
    days = response.json()["days"]
    assert len(days) == 7
    for offset, day in enumerate(days):
        for lesson, (start, end) in zip(day["lessons"], WEEKDAY if offset < 5 else WEEKEND, strict=True):
            assert (lesson["start_time"], lesson["end_time"]) == (start, end)
            assert lesson["starts_at"][11:16] == start
            assert lesson["ends_at"][11:16] == end

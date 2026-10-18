from pathlib import Path

import pytest


@pytest.fixture
def html_path():
    return Path(__file__).parent / "fixtures" / "timetable.html"


@pytest.fixture
def html(html_path):
    return html_path.read_text(encoding="utf-8")

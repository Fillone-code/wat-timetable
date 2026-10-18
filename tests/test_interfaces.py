import json

import httpx
import pytest
from fastapi.testclient import TestClient

from watplan.api import create_app
from watplan.cli import main
from watplan.client import TimetableClient
from watplan.service import TimetableService


def test_cli_json_has_no_extra_stdout(html_path, capsys):
    code = main(["plan", "DEMO25CS1", "--html", str(html_path), "--date", "2025-10-20", "--json"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["lesson_count"] == 2
    assert payload["period"] == "day"


def test_cli_json_errors_go_to_stderr(html_path, capsys):
    code = main(["plan", "DEMO25CS1", "--html", str(html_path), "--period", "day", "--json"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("argv", [
    ["plan", "DEMO25CS1", "--date", "bad", "--json"],
    ["plan", "DEMO25CS1", "--period", "bad", "--format=json"],
    ["plan", "--json"],
])
def test_cli_syntax_errors_are_json(argv, capsys):
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "invalid_request"


def test_cli_readable_table(html_path, capsys):
    assert main(["plan", "DEMO25CS1", "--html", str(html_path), "--date", "2025-10-20"]) == 0
    captured = capsys.readouterr()
    assert "Monday" in captured.out
    assert "Intro" in captured.out
    assert "08:00" in captured.out
    assert "Wykład" in captured.out


@pytest.mark.parametrize("command,key", [("groups", "groups"), ("semesters", "semesters")])
def test_cli_catalog(command, key, html_path, capsys):
    assert main([command, "--group", "DEMO25CS1", "--html", str(html_path), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)[key]) == 4


def test_api_end_to_end_and_openapi(html_path):
    with TestClient(create_app(html_path=html_path)) as api:
        response = api.get("/plan", params={"group": "DEMO25CS1", "period": "day", "date": "2025-10-20"})
        assert response.status_code == 200
        assert response.json()["lesson_count"] == 2
        assert response.json()["schema_version"] == "1.0"
        assert api.get("/health").json() == {"status": "ok"}
        assert api.get("/groups", params={"group": "DEMO25CS1", "search": "cs"}).json() == {"groups": ["DEMO25CS1", "DEMO25CS2"]}
        assert len(api.get("/semesters", params={"group": "DEMO25CS1"}).json()["semesters"]) == 4
        assert api.get("/docs").status_code == 200
        assert "Schedule" in api.get("/openapi.json").json()["components"]["schemas"]


@pytest.mark.parametrize("params,status", [
    ({}, 422),
    ({"group": "DEMO25CS1", "period": "day"}, 400),
    ({"group": "DEMO25CS1", "period": "day", "date": "not-a-date"}, 422),
    ({"group": "DEMO25CS1", "period": "bad"}, 422),
    ({"group": "DEMO25CS1", "semester": "winter-1999"}, 404),
    ({"group": "DEMO25CS9"}, 404),
    ({"group": "http://example.com"}, 400),
])
def test_api_validation_errors_are_structured(params, status, html_path):
    with TestClient(create_app(html_path=html_path)) as api:
        response = api.get("/plan", params=params)
        assert response.status_code == status
        assert response.json()["error"]["code"]
        assert response.json()["error"]["message"]


def test_api_upstream_failure_returns_502():
    client = TimetableClient(transport=httpx.MockTransport(lambda _: httpx.Response(403)))
    service = TimetableService(client)
    try:
        with TestClient(create_app(service=service)) as api:
            response = api.get("/plan", params={"group": "DEMO25CS1"})
            assert response.status_code == 502
            assert response.json()["error"]["code"] == "source_unavailable"
    finally:
        service.close()

import json
from datetime import datetime, timedelta

import httpx
import pytest

from watplan.client import TimetableClient
from watplan.errors import SourceError
from watplan.models import WARSAW


def test_disk_cache_is_shared_between_cli_processes_and_keyed_by_term(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=f"page {request.url.params.get('date', 'default')}")

    transport = httpx.MockTransport(handler)
    first = TimetableClient(transport=transport, cache_dir=tmp_path)
    try:
        page = first.fetch("DEMO25CS1", 1790546400)
    finally:
        first.close()
    second = TimetableClient(transport=transport, cache_dir=tmp_path)
    try:
        reused = second.fetch("DEMO25CS1", 1790546400)
        assert reused == page
        assert len(requests) == 1
        assert second.fetch("DEMO25CS1", 1771801200).html == "page 1771801200"
        assert len(requests) == 2
        second.fetch("DEMO25CS1", 1790546400, refresh=True)
        assert len(requests) == 3
    finally:
        second.close()


@pytest.mark.parametrize("damage", ["expired", "corrupt", "wrong-url"])
def test_bad_or_expired_disk_entries_are_refetched(tmp_path, damage):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, text=f"page {count}")

    transport = httpx.MockTransport(handler)
    first = TimetableClient(transport=transport, cache_dir=tmp_path)
    try:
        first.fetch("DEMO25CS1")
    finally:
        first.close()
    cached = next(tmp_path.glob("*.json"))
    payload = json.loads(cached.read_text())
    if damage == "expired":
        payload["fetched_at"] = (datetime.now(WARSAW) - timedelta(minutes=6)).isoformat()
    if damage == "wrong-url":
        payload["request_url"] = "some other semester"
    cached.write_text("not json" if damage == "corrupt" else json.dumps(payload), encoding="utf-8")
    second = TimetableClient(transport=transport, cache_dir=tmp_path)
    try:
        assert second.fetch("DEMO25CS1").html == "page 2"
        assert count == 2
    finally:
        second.close()


def test_unwritable_cache_does_not_break_fetching(tmp_path):
    file = tmp_path / "not-a-directory"
    file.write_text("occupied")
    client = TimetableClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="ok")), cache_dir=file)
    try:
        assert client.fetch("DEMO25CS1").html == "ok"
    finally:
        client.close()


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit_and_block_errors_are_not_cached_as_pages(tmp_path, status):
    client = TimetableClient(transport=httpx.MockTransport(lambda _: httpx.Response(status)), cache_dir=tmp_path)
    try:
        with pytest.raises(SourceError):
            client.fetch("DEMO25CS1")
        assert not list(tmp_path.glob("*.json"))
    finally:
        client.close()

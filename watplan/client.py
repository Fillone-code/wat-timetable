from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from .errors import SourceError
from .models import WARSAW

SOURCE_URL = "https://planzajec.wcy.wat.edu.pl/pl/rozklad"


def source_url(group: str, timestamp: int | None = None) -> str:
    params = {"grupa_id": group}
    if timestamp is not None:
        params["date"] = str(timestamp)
    return str(httpx.URL(SOURCE_URL, params=params))


def default_cache_dir() -> Path | None:
    if "WATPLAN_CACHE_DIR" in os.environ:
        value = os.environ["WATPLAN_CACHE_DIR"]
        return Path(value).expanduser() if value else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "watplan"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "watplan" / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "watplan"


@dataclass(frozen=True)
class SourcePage:
    html: str
    url: str
    fetched_at: datetime
    mode: str = "live"


class TimetableClient:
    """Fixed-origin HTTP client with a five-minute memory and disk cache."""

    def __init__(self, *, html_path: str | Path | None = None, ttl: float = 300,
                 transport: httpx.BaseTransport | None = None, cache_dir: str | Path | None = None):
        self.html_path = Path(html_path) if html_path else None
        self.ttl = ttl
        # Mock transports and HTML imports never touch the user's real cache.
        self.cache_dir = Path(cache_dir) if cache_dir is not None else (
            default_cache_dir() if transport is None and self.html_path is None else None
        )
        self._cache: dict[str, tuple[float, SourcePage]] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False,
            headers={"User-Agent": "watplan/0.1 (+local timetable CLI)", "Accept": "text/html"},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _remember(self, url: str, page: SourcePage, age: float = 0) -> None:
        self._cache[url] = time.monotonic() - age, page
        if len(self._cache) > 256:
            oldest = min(self._cache, key=lambda key: self._cache[key][0])
            del self._cache[oldest]

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def _read_disk(self, url: str) -> SourcePage | None:
        if self.cache_dir is None or self.ttl <= 0:
            return None
        try:
            payload = json.loads(self._cache_path(url).read_text(encoding="utf-8"))
            if payload["request_url"] != url:
                return None
            fetched = datetime.fromisoformat(payload["fetched_at"])
            age = time.time() - fetched.timestamp()
            if fetched.tzinfo is None or not 0 <= age < self.ttl:
                return None
            if not isinstance(payload["html"], str) or not isinstance(payload["url"], str):
                return None
            page = SourcePage(payload["html"], payload["url"], fetched)
            self._remember(url, page, age)
            return page
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return None

    def _write_disk(self, url: str, page: SourcePage) -> None:
        if self.cache_dir is None or self.ttl <= 0:
            return
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir,
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"request_url": url, "url": page.url, "fetched_at": page.fetched_at.isoformat(),
                           "html": page.html}, stream, ensure_ascii=False)
            temporary.replace(self._cache_path(url))
            # Bound the shared cache as well as the per-process one.
            files = sorted(self.cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
            for stale in files[:-256]:
                stale.unlink(missing_ok=True)
        except OSError:
            # A read-only/full cache directory must not break fetching.
            pass
        finally:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def fetch(self, group: str, timestamp: int | None = None, *, refresh: bool = False) -> SourcePage:
        url = source_url(group, timestamp)
        if self.html_path:
            try:
                html = self.html_path.read_text(encoding="utf-8-sig")
                fetched = datetime.fromtimestamp(self.html_path.stat().st_mtime, WARSAW)
            except (OSError, UnicodeError) as exc:
                raise SourceError(f"Could not read saved HTML: {exc}") from exc
            return SourcePage(html, url, fetched, "html")
        # Hold this lock through the request to coalesce concurrent API reads.
        with self._lock:
            cached = self._cache.get(url)
            if cached and not refresh and time.monotonic() - cached[0] < self.ttl:
                return cached[1]
            if not refresh:
                saved = self._read_disk(url)
                if saved is not None:
                    return saved
            try:
                response = self._http.get(url)
                # Some servers redirect a GET to its canonical URL. Follow only
                # same-origin redirects, never an arbitrary location from HTML.
                for _ in range(3):
                    if not response.is_redirect:
                        break
                    target = response.next_request
                    if target is None or target.url.host != httpx.URL(SOURCE_URL).host or target.url.scheme != "https":
                        raise SourceError("The timetable source redirected outside its HTTPS origin.")
                    response = self._http.send(target)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    raise SourceError(
                        f"WAT blocked or rate-limited this request (HTTP {exc.response.status_code}). "
                        "Use --html with a timetable page saved from a browser that can access the site."
                    ) from exc
                raise SourceError(f"WAT returned HTTP {exc.response.status_code}.") from exc
            except httpx.RequestError as exc:
                raise SourceError(f"Could not reach the WAT timetable: {exc}") from exc
            page = SourcePage(response.text, str(response.url), datetime.now(WARSAW))
            self._remember(url, page)
            self._write_disk(url, page)
            return page

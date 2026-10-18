# watplan

A developer tool for the [WCY WAT timetable](https://planzajec.wcy.wat.edu.pl/pl/rozklad). Enter a group name and semester to view a readable terminal timetable, get JSON, or use a local HTTP API.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run watplan plan GROUP_ID
uv run watplan plan GROUP_ID --semester 2026-2027-winter --period next-week
uv run watplan plan GROUP_ID --period week --date 2026-10-21 --json
uv run watplan semesters --group GROUP_ID
uv run watplan groups --group GROUP_ID --search GROUP
```

Replace `GROUP_ID` with a group name listed on the WAT timetable. Semester IDs are read from the site rather than guessed. `current` selects the latest published semester whose start date has passed. You can also use IDs like `2025-2026-winter`, shortcuts like `winter-2025` (the first year of the academic year), or a source timestamp returned by `semesters`. Here, semester means an academic term, not a numbered study semester.

## Date filters

| Period | Meaning | Extra inputs |
| --- | --- | --- |
| `this-week` | This Monday through Sunday; default | None |
| `next-week` | Next Monday through Sunday | None |
| `today` / `tomorrow` | One calendar day | None |
| `day` | One specific date | `--date YYYY-MM-DD` |
| `week` | Monday–Sunday containing a date | `--date YYYY-MM-DD` |
| `range` | Inclusive date range | `--start` and `--end` |
| `semester` | All lessons in the selected term | None |

Relative periods use the actual date in Europe/Warsaw. Choosing an old semester does not change what “this week” means. Lesson timestamps include the Warsaw UTC offset, including daylight-saving changes. Only dates with lessons appear in `days`; empty periods return zero lessons and the dates available in the source.

## JSON and scripting

Use `--json` or `--format json` on `plan`, `groups`, and `semesters`. Successful JSON is written to stdout without banners or logs. Errors are JSON on stderr with exit code 2.

```sh
uv run watplan plan GROUP_ID --period next-week --json > plan.json
uv run watplan plan GROUP_ID --period next-week --json | jq '.days[].lessons[].subject'
```

```json
{
  "date": "2026-10-19",
  "block": 1,
  "start_time": "08:00",
  "end_time": "09:35",
  "starts_at": "2026-10-19T08:00:00+02:00",
  "ends_at": "2026-10-19T09:35:00+02:00",
  "subject": "Introduction to programming",
  "subject_short": "Intro",
  "lesson_type": "Wykład",
  "lesson_type_short": "w",
  "room": "Lab A-101",
  "instructors": ["Jordan Example"],
  "occurrence": 2
}
```

The top-level response includes `schema_version`, `group`, `semester`, `timezone`, `period`, `date_range`, `available_dates`, `lesson_count`, `days`, `source`, and `warnings`. Missing rooms are `null`; missing lecturers are an empty array. Duplicate records for the same lesson are merged by instructor, while classes in different rooms stay separate.

## HTTP API

```sh
uv run watplan serve
```

Interactive documentation is at <http://127.0.0.1:8000/docs>; OpenAPI is at `/openapi.json`.

```sh
curl 'http://127.0.0.1:8000/plan?group=GROUP_ID&semester=2026-2027-winter&period=next-week'
curl 'http://127.0.0.1:8000/plan?group=GROUP_ID&period=day&date=2026-10-21'
curl 'http://127.0.0.1:8000/groups?group=GROUP_ID&search=GROUP'
curl 'http://127.0.0.1:8000/semesters?group=GROUP_ID'
curl 'http://127.0.0.1:8000/health'
```

The API always returns JSON. Dates require `period=day` or `week`. Errors use a structured `error` object. Source responses are cached for five minutes in memory and on disk; repeated commands can reuse a recent response without contacting WAT. There are no automatic retries or background polls. Use `--refresh` only when a fresh fetch is needed.

## Python

```python
from datetime import date
from watplan import TimetableService

service = TimetableService()
try:
    schedule = service.get_schedule(
        "GROUP_ID", semester="2026-2027-winter", period="day", on=date(2026, 10, 21)
    )
    print(schedule.model_dump_json(indent=2))
finally:
    service.close()
```

## Saved HTML and offline use

WAT can deny automated requests with HTTP 403 or serve a security challenge. If you can view the timetable in your browser, save the selected group and semester page as UTF-8 HTML and pass it with `--html`; this makes no network requests and only serves the saved snapshot.

```sh
uv run watplan plan GROUP_ID --html /path/to/timetable.html --period semester
uv run watplan serve --html /path/to/timetable.html
```

## Development

```sh
uv sync
uv run pytest -q
```

Tests use local synthetic fixtures and mocked requests; they never contact WAT. The sample markup contains fictional groups, courses, instructors, rooms, and dates. Try it offline with:

```sh
uv run watplan plan DEMO25CS1 --html tests/fixtures/multi-semester.html --date 2026-10-19
```

## License

The project code is available under the MIT License. It applies to this code, not timetable content or the WAT website, which remain subject to their owners' terms.

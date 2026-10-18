from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from functools import partial
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from .client import TimetableClient
from .errors import WatplanError
from .models import Schedule
from .service import PERIODS, TimetableService


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an ISO date: YYYY-MM-DD.") from exc


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, json_errors: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.json_errors = json_errors

    def error(self, message: str) -> None:
        # Keep stdout machine-readable even for syntax/validation failures.
        if self.json_errors:
            print(json.dumps({"error": {"code": "invalid_request", "message": message}}, ensure_ascii=False), file=sys.stderr)
            raise SystemExit(2)
        super().error(message)


def add_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--html", type=Path, help="Parse a saved WAT timetable HTML file; no network requests")
    parser.add_argument("--refresh", action="store_true", help="Bypass the memory and disk source caches")
    parser.add_argument("--format", choices=("table", "json"), default="table", help="Output format (default: table)")
    parser.add_argument("--json", action="store_true", help="Shortcut for --format json")


def build_parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser = Parser(prog="watplan", json_errors=json_errors, description="WCY WAT timetables in your terminal, as JSON, or over HTTP.")
    parser.add_argument("--version", action="version", version=f"watplan {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=partial(Parser, json_errors=json_errors))
    plan = sub.add_parser("plan", help="Get a group's timetable")
    plan.add_argument("group", help="Group name (case insensitive)")
    plan.add_argument("--semester", default="current", help="current, winter-2025, or 2025-2026-winter; see semesters")
    plan.add_argument("--period", choices=PERIODS, help="Default: this-week, or day/range when dates are provided")
    plan.add_argument("--date", type=iso_date, help="A day or a date in the desired week")
    plan.add_argument("--start", type=iso_date, help="Inclusive start of a custom range")
    plan.add_argument("--end", type=iso_date, help="Inclusive end of a custom range")
    add_source_options(plan)
    groups = sub.add_parser("groups", help="List/search the group names published by WAT")
    groups.add_argument("--group", required=True, help="Group whose timetable lists group names")
    groups.add_argument("--search", default="", help="Case-insensitive substring to match")
    add_source_options(groups)
    semesters = sub.add_parser("semesters", help="List available semester IDs")
    semesters.add_argument("--group", required=True, help="Group whose timetable lists semesters")
    add_source_options(semesters)
    serve = sub.add_parser("serve", help="Run the HTTP API and its interactive /docs")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--html", type=Path, help="Serve a saved timetable snapshot without fetching WAT")
    return parser


def render_schedule(schedule: Schedule, console: Console) -> None:
    console.print(Text(f"{schedule.group}  /  {schedule.semester.label}", style="bold cyan"))
    window = schedule.date_range
    label = f"{window.start} → {window.end}" if window.start else "Full semester"
    console.print(f"{label}  ·  Europe/Warsaw  ·  {schedule.lesson_count} lessons", style="dim", markup=False)
    for warning in schedule.warnings:
        console.print(Text(warning, style="yellow"))
    for day in schedule.days:
        console.print()
        console.print(Text(f"{day.weekday}, {day.date:%d %B %Y}", style="bold"))
        table = Table(box=box.SIMPLE_HEAVY, expand=False, padding=(0, 1), show_edge=False)
        table.add_column("Time", no_wrap=True, style="cyan")
        table.add_column("Subject", min_width=20, max_width=54)
        table.add_column("Type", max_width=22)
        table.add_column("Room", max_width=20)
        table.add_column("Lecturer", max_width=35)
        for lesson in day.lessons:
            when = f"{lesson.start_time}–{lesson.end_time}" if lesson.start_time else f"Block {lesson.block}"
            table.add_row(*[Text(value) for value in (
                when, lesson.subject, lesson.lesson_type, lesson.room or "—", ", ".join(lesson.instructors) or "—"
            )])
        console.print(table)
    if not schedule.days:
        console.print("\nNo scheduled lessons in this period.")
    console.print()
    console.print(Text(f"Source: {schedule.source.url}", style="dim"))
    if schedule.source.updated_at:
        console.print(f"WAT updated: {schedule.source.updated_at}", style="dim", markup=False)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    json_errors = "--json" in argv or "--format=json" in argv or any(
        left == "--format" and right == "json" for left, right in zip(argv, argv[1:])
    )
    args = build_parser(json_errors=json_errors).parse_args(argv)
    if args.command == "serve":
        import uvicorn
        from .api import create_app

        uvicorn.run(create_app(html_path=args.html), host=args.host, port=args.port)
        return 0
    as_json = args.json or args.format == "json"
    console = Console(highlight=False)
    service = TimetableService(TimetableClient(html_path=args.html))
    try:
        if args.command == "plan":
            period = args.period or ("day" if args.date else "range" if args.start or args.end else "this-week")
            schedule = service.get_schedule(
                args.group, semester=args.semester, period=period, on=args.date,
                start=args.start, end=args.end, refresh=args.refresh,
            )
            if as_json:
                print(schedule.model_dump_json(indent=2))
            else:
                render_schedule(schedule, console)
        else:
            catalog = service.catalog(args.group, refresh=args.refresh)
            if args.command == "groups":
                groups = [group for group in catalog.groups if args.search.casefold() in group.casefold()]
                if as_json:
                    print(json.dumps({"groups": groups}, ensure_ascii=False, indent=2))
                else:
                    for group in groups:
                        console.print(group, markup=False)
                    console.print(f"{len(groups)} groups", style="dim")
            elif as_json:
                print(json.dumps({"semesters": [s.model_dump(mode="json") for s in catalog.semesters]}, ensure_ascii=False, indent=2))
            else:
                table = Table("Semester ID", "WAT label", "Starts", box=box.SIMPLE_HEAVY)
                for semester in reversed(catalog.semesters):
                    table.add_row(semester.id, semester.label, semester.start_date.isoformat())
                console.print(table)
        return 0
    except WatplanError as exc:
        if as_json:
            print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        else:
            Console(stderr=True).print(Text(f"Error: {exc}", style="red"))
        return 2
    except BrokenPipeError:
        return 0
    finally:
        service.close()


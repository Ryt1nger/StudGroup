"""Private, read-only local HTML check. Never prints source HTML or lesson/person data."""

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from studgroup.ranepa_probe import MAX_PAGE_BYTES, SourceAccessError, fetch_public_page
from studgroup.ranepa_public import extract_public_schedule
from studgroup.ranepa_schedule import group_code

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def inspect_html(html: str, code: str, monday: date) -> dict:
    try:
        result = extract_public_schedule(html, code, monday, monday + timedelta(days=6))
    except SourceAccessError as error:
        return {"status": "parser_unverified", "reason": str(error)}
    return {
        "status": "preview_only" if result.lessons else "parser_unverified",
        "complete": False,
        "lesson_count": len(result.lessons),
        "days_with_lessons": len({row.day for row in result.lessons}),
    }


def local_html(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("html_symlink_not_allowed")
    source = path.expanduser().resolve(strict=True)
    if source.is_relative_to(REPOSITORY_ROOT):
        raise ValueError("html_inside_repository_not_allowed")
    if source.stat().st_size > MAX_PAGE_BYTES:
        raise ValueError("html_too_large")
    return source.read_text(encoding="utf-8")


async def direct_http(group_uuid: UUID, code: str, monday: date) -> dict:
    # This deliberately sends no browser cookies or credentials.
    try:
        probe, html = await fetch_public_page(group_uuid, code)
    except SourceAccessError as error:
        return {"status": "http_blocked", "reason": str(error)}
    result = inspect_html(html, code, monday)
    return {
        "http_status": "html_received",
        "candidate_time_ranges": probe.pair_time_ranges,
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Private read-only RANEPA HTML parser check")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--html-file", type=Path, help="private HTML file outside repository")
    source.add_argument(
        "--direct-http", type=UUID, metavar="GROUP_UUID", help="unauthenticated HTTP check"
    )
    parser.add_argument("--group-code", default="Э-ОБ-385-04-26-02")
    parser.add_argument("--week-start", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.week_start.weekday() != 0:
        parser.error("--week-start must be a Monday")
    code = group_code(args.group_code)
    try:
        result = (
            inspect_html(local_html(args.html_file), code, args.week_start)
            if args.html_file
            else asyncio.run(direct_http(args.direct_http, code, args.week_start))
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

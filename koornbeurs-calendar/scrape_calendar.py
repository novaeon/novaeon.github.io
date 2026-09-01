#!/usr/bin/env python3
"""Turn the Koornbeurs agenda page into a subscribable iCalendar feed."""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import os
import re
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


SOURCE_URL = "https://koornbeurs.nl/agenda/"
CALENDAR_NAME = "Koornbeurs Agenda"
LOCATION = "O.J.V. De Koornbeurs, Voldersgracht 1, 2611 ET Delft"
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

MONTHS = {
    "january": 1,
    "jan": 1,
    "januari": 1,
    "february": 2,
    "feb": 2,
    "februari": 2,
    "march": 3,
    "mar": 3,
    "maart": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "mei": 5,
    "june": 6,
    "jun": 6,
    "juni": 6,
    "july": 7,
    "jul": 7,
    "juli": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

WEEKDAYS = {
    "monday": 0,
    "maandag": 0,
    "tuesday": 1,
    "dinsdag": 1,
    "wednesday": 2,
    "woensdag": 2,
    "thursday": 3,
    "donderdag": 3,
    "friday": 4,
    "vrijdag": 4,
    "saturday": 5,
    "zaterdag": 5,
    "sunday": 6,
    "zondag": 6,
}

MONTH_PATTERN = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
WEEKDAY_PATTERN = "|".join(sorted(map(re.escape, WEEKDAYS), key=len, reverse=True))
DATE_PART_RE = re.compile(
    rf"(?:(?P<weekday>{WEEKDAY_PATTERN})\s+)?"
    rf"(?:(?P<month>{MONTH_PATTERN})\s+)?"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th|e|de)?"
    r"(?:,?\s*(?P<year>20\d{2}))?",
    re.IGNORECASE,
)
HAS_MONTH_RE = re.compile(rf"\b(?:{MONTH_PATTERN})\b", re.IGNORECASE)
TIME_RANGE_RE = re.compile(
    r"(?P<sh>[01]?\d|2[0-3])[:.](?P<sm>[0-5]\d)\s*"
    r"[-\u2012\u2013\u2014]\s*"
    r"(?P<eh>[01]?\d|2[0-3])[:.](?P<em>[0-5]\d)"
)


@dataclass(frozen=True)
class Anchor:
    year: int
    month: int


@dataclass(frozen=True)
class Event:
    title: str
    event_date: date
    start: time | None
    end: time | None
    description: str


def fetch_html(url: str) -> tuple[str, datetime]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KoornbeursCalendar/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    return body, datetime.now(timezone.utc)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(value)).strip()


def source_modified_time(soup: BeautifulSoup, fallback: datetime) -> datetime:
    tag = soup.select_one('meta[property="article:modified_time"]')
    if tag and tag.get("content"):
        try:
            parsed = datetime.fromisoformat(str(tag["content"]).replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return fallback.astimezone(timezone.utc)


def find_anchor(soup: BeautifulSoup, fallback: datetime) -> Anchor:
    # The page currently includes images named like "Monthposter Sep 2026".
    # This is a safer year source than guessing from today's date.
    for image in soup.select("img"):
        haystack = " ".join(
            str(image.get(attribute, "")) for attribute in ("title", "alt", "src")
        )
        match = re.search(
            rf"\b(?P<month>{MONTH_PATTERN})[\s_-]+(?P<year>20\d{{2}})\b",
            haystack,
            re.IGNORECASE,
        )
        if match:
            return Anchor(int(match.group("year")), MONTHS[match.group("month").lower()])
    local = fallback.astimezone(LOCAL_TZ)
    return Anchor(local.year, local.month)


def infer_year(month: int, anchor: Anchor) -> int:
    # A December poster can legitimately list January events. A small backwards
    # step is treated as an older event still left on the page, not next year.
    if anchor.month - month >= 6:
        return anchor.year + 1
    return anchor.year


def parse_dates(text: str, anchor: Anchor, warnings: list[str]) -> list[date]:
    if not HAS_MONTH_RE.search(text):
        return []

    parts = re.split(r"\s+(?:and|en)\s+|\s*[&|]\s*", text, flags=re.IGNORECASE)
    parsed: list[date] = []
    inherited_month: int | None = None

    for part in parts:
        match = DATE_PART_RE.search(part.strip())
        if not match:
            continue
        month_name = match.group("month")
        month = MONTHS[month_name.lower()] if month_name else inherited_month
        if month is None:
            continue
        inherited_month = month
        year = int(match.group("year")) if match.group("year") else infer_year(month, anchor)
        try:
            parsed_date = date(year, month, int(match.group("day")))
        except ValueError as error:
            warnings.append(f"Invalid date in {text!r}: {error}")
            continue

        weekday_name = match.group("weekday")
        if weekday_name and WEEKDAYS[weekday_name.lower()] != parsed_date.weekday():
            actual = parsed_date.strftime("%A")
            warnings.append(
                f"Weekday mismatch in {text!r}: {parsed_date.isoformat()} is {actual}, "
                f"not {weekday_name}. Numeric date kept."
            )
        parsed.append(parsed_date)
    return parsed


def parse_time_ranges(text: str) -> list[tuple[time, time]]:
    ranges: list[tuple[time, time]] = []
    for match in TIME_RANGE_RE.finditer(text):
        ranges.append(
            (
                time(int(match.group("sh")), int(match.group("sm"))),
                time(int(match.group("eh")), int(match.group("em"))),
            )
        )
    return ranges


def paragraph_lines(container) -> list[str]:
    lines: list[str] = []
    for paragraph in container.select("p"):
        value = clean_text(paragraph.get_text(" ", strip=True))
        if value:
            lines.append(value)
    return lines


def parse_events(page_html: str, fetched_at: datetime) -> tuple[list[Event], list[str], datetime]:
    soup = BeautifulSoup(page_html, "html.parser")
    anchor = find_anchor(soup, fetched_at)
    modified = source_modified_time(soup, fetched_at)
    warnings: list[str] = []
    events: list[Event] = []

    content_roots = soup.select(".entry-content")
    # WordPress can emit an empty entry-content wrapper before the real page.
    # Pick the candidate containing the most Brizy rows.
    root = (
        max(content_roots, key=lambda candidate: len(candidate.select(".brz-row__container")))
        if content_roots
        else soup
    )
    rows = root.select(".brz-row__container")
    if not rows:
        warnings.append("No Brizy event rows found; the page layout may have changed.")

    for row in rows:
        lines = paragraph_lines(row)
        if len(lines) < 2:
            continue

        date_index = -1
        dates: list[date] = []
        row_warnings: list[str] = []
        for index, line in enumerate(lines):
            candidate_warnings: list[str] = []
            candidate = parse_dates(line, anchor, candidate_warnings)
            if candidate:
                date_index = index
                dates = candidate
                row_warnings.extend(candidate_warnings)
                break
        if date_index <= 0:
            continue

        title = lines[date_index - 1]
        time_index: int | None = None
        time_ranges: list[tuple[time, time]] = []
        # Times are expected immediately after the date. Keeping this window
        # narrow avoids mistaking a later "Doors: 20:00" note for event timing.
        for index in range(date_index + 1, min(date_index + 3, len(lines))):
            candidate = parse_time_ranges(lines[index])
            if candidate:
                time_index = index
                time_ranges = candidate
                break

        if len(time_ranges) not in (0, 1, len(dates)):
            warnings.append(
                f"{title!r}: found {len(dates)} dates but {len(time_ranges)} time ranges; "
                "using the first time range for every date."
            )

        description_start = (time_index + 1) if time_index is not None else (date_index + 1)
        description = "\n".join(lines[description_start:])
        warnings.extend(f"{title!r}: {warning}" for warning in row_warnings)

        for index, event_date in enumerate(dates):
            if not time_ranges:
                start = end = None
            elif len(time_ranges) == len(dates):
                start, end = time_ranges[index]
            else:
                start, end = time_ranges[0]
            events.append(Event(title, event_date, start, end, description))

    # Avoid duplicated rows if a future page builder emits desktop/mobile copies.
    unique: dict[tuple[str, date, time | None, time | None], Event] = {}
    for event in events:
        unique[(event.title.casefold(), event.event_date, event.start, event.end)] = event
    return sorted(unique.values(), key=event_sort_key), warnings, modified


def event_sort_key(event: Event) -> tuple[date, time, str]:
    return event.event_date, event.start or time.min, event.title.casefold()


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_ics_line(line: str) -> list[str]:
    """Fold an iCalendar content line to at most 75 UTF-8 octets."""
    result: list[str] = []
    remaining = line
    first = True
    while remaining:
        limit = 75 if first else 74  # Continuation space counts as one octet.
        byte_count = 0
        cut = 0
        for cut, character in enumerate(remaining, start=1):
            size = len(character.encode("utf-8"))
            if byte_count + size > limit:
                cut -= 1
                break
            byte_count += size
        else:
            cut = len(remaining)
        if cut <= 0:
            cut = 1
        chunk, remaining = remaining[:cut], remaining[cut:]
        result.append(chunk if first else " " + chunk)
        first = False
    return result or [""]


def event_uid(event: Event) -> str:
    identity = f"{SOURCE_URL}|{event.title.casefold()}|{event.event_date.isoformat()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{digest}@koornbeurs-calendar"


def utc_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_lines(event: Event, modified: datetime) -> Iterable[str]:
    yield "BEGIN:VEVENT"
    yield f"UID:{event_uid(event)}"
    yield f"DTSTAMP:{utc_stamp(modified)}"
    yield f"LAST-MODIFIED:{utc_stamp(modified)}"

    if event.start is None or event.end is None:
        yield f"DTSTART;VALUE=DATE:{event.event_date:%Y%m%d}"
        yield f"DTEND;VALUE=DATE:{event.event_date + timedelta(days=1):%Y%m%d}"
    else:
        start = datetime.combine(event.event_date, event.start, LOCAL_TZ)
        end_date = event.event_date + (timedelta(days=1) if event.end <= event.start else timedelta())
        end = datetime.combine(end_date, event.end, LOCAL_TZ)
        yield f"DTSTART:{utc_stamp(start)}"
        yield f"DTEND:{utc_stamp(end)}"

    yield f"SUMMARY:{ics_escape(event.title)}"
    description = event.description.strip()
    if description:
        description += "\n\n"
    description += f"Source: {SOURCE_URL}"
    yield f"DESCRIPTION:{ics_escape(description)}"
    yield f"LOCATION:{ics_escape(LOCATION)}"
    yield f"URL:{SOURCE_URL}"
    yield "STATUS:CONFIRMED"
    yield "TRANSP:OPAQUE"
    yield "END:VEVENT"


def build_ics(events: list[Event], modified: datetime) -> str:
    raw_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Koornbeurs Calendar//Agenda Scraper 1.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        "X-WR-TIMEZONE:Europe/Amsterdam",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for event in events:
        raw_lines.extend(event_lines(event, modified))
    raw_lines.append("END:VCALENDAR")

    folded = [piece for line in raw_lines for piece in fold_ics_line(line)]
    return "\r\n".join(folded) + "\r\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as temporary:
            temporary.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--input-html", type=Path, help="Parse a saved page instead of downloading it")
    parser.add_argument("--output", type=Path, default=Path("docs/koornbeurs.ics"))
    parser.add_argument("--min-events", type=int, default=1)
    args = parser.parse_args()

    fetched_at = datetime.now(timezone.utc)
    if args.input_html:
        page_html = args.input_html.read_text(encoding="utf-8")
    else:
        page_html, fetched_at = fetch_html(args.url)

    events, warnings, modified = parse_events(page_html, fetched_at)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if len(events) < args.min_events:
        print(
            f"ERROR: Parsed {len(events)} event(s), fewer than required minimum "
            f"{args.min_events}; existing calendar was not overwritten.",
            file=sys.stderr,
        )
        return 1

    atomic_write(args.output, build_ics(events, modified))
    print(f"Wrote {len(events)} event(s) to {args.output}")
    for event in events:
        timing = "all day" if event.start is None else f"{event.start:%H:%M}-{event.end:%H:%M}"
        print(f"  {event.event_date.isoformat()} {timing}  {event.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

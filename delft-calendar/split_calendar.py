"""Split an iCalendar feed without rebuilding or guessing its event details."""

from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import tempfile
import tomllib
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from icalendar import Calendar

MAX_BYTES = 15 * 1024 * 1024


def validate_config(config):
    courses = config.get("courses", {})
    if not courses:
        raise ValueError("At least one course is required")
    used_codes = set()
    for slug, rule in courses.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug) or slug == "misc":
            raise ValueError("Course IDs must be safe lowercase filenames, excluding misc")
        if not isinstance(rule.get("name"), str) or not rule["name"].strip():
            raise ValueError("Each course needs a name")
        codes = rule.get("codes")
        if not isinstance(codes, list) or not codes:
            raise ValueError("Each course needs a nonempty codes list")
        for code in codes:
            if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", code):
                raise ValueError("Course codes must contain only letters, numbers, _ or -")
            if code.upper() in used_codes:
                raise ValueError("A course code cannot appear in two rules")
            used_codes.add(code.upper())
    routing = config.get("routing", {})
    fields = routing.get("fields", ["SUMMARY", "DESCRIPTION"])
    if not isinstance(fields, list) or not fields or any(
        field not in ("SUMMARY", "DESCRIPTION", "CATEGORIES", "LOCATION") for field in fields
    ):
        raise ValueError("Invalid matching fields")
    re.compile(routing.get("shared_pattern", r"(?i)\bshared\s+lab\b"))
    if not isinstance(routing.get("misc_name", "Shared labs & Misc"), str):
        raise ValueError("misc_name must be text")


def matching_text(event, config):
    fields = config.get("routing", {}).get("fields", ["SUMMARY", "DESCRIPTION"])
    return "\n".join(str(event.get(field, "")) for field in fields)


def classify(events, config):
    # Keep a recurring series and every modified/cancelled instance together.
    text = "\n".join(matching_text(event, config) for event in events)
    shared = config.get("routing", {}).get("shared_pattern", r"(?i)\bshared\s+lab\b")
    if shared and re.search(shared, text):
        return "misc"
    matches = [slug for slug, rule in config["courses"].items() if any(
        re.search(r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])", text, re.I)
        for code in rule["codes"]
    )]
    return matches[0] if len(matches) == 1 else "misc"


def split_feed(data, config):
    validate_config(config)
    if len(data) > MAX_BYTES or not data.strip().startswith(b"BEGIN:VCALENDAR") or not data.strip().endswith(b"END:VCALENDAR"):
        raise ValueError("Source is not a complete iCalendar file")
    source = Calendar.from_ical(data)
    if any(component.errors for component in source.walk()):
        raise ValueError("Source has malformed calendar properties")
    events = source.walk("VEVENT")
    if not events:
        raise ValueError("Source contains no events; keeping the previous feeds")
    families = defaultdict(list)
    for event in events:
        if not event.get("UID"):
            raise ValueError("Event has no UID")
        if not event.get("DTSTART") and str(event.get("STATUS", "")) != "CANCELLED":
            raise ValueError("Non-cancelled event has no start date")
        families[str(event["UID"])].append(event)

    names = {slug: rule["name"] for slug, rule in config["courses"].items()}
    names["misc"] = config.get("routing", {}).get("misc_name", "Shared labs & Misc")
    calendars = {}
    for slug, name in names.items():
        calendar = Calendar()
        calendar.add("PRODID", "-//Timetable Splitter//EN")
        calendar.add("VERSION", "2.0")
        calendar.add("CALSCALE", "GREGORIAN")
        calendar.add("METHOD", "PUBLISH")
        calendar.add("X-WR-CALNAME", name)
        calendar.add("X-PUBLISHED-TTL", "PT6H")
        if source.get("X-WR-TIMEZONE"):
            calendar["X-WR-TIMEZONE"] = copy.deepcopy(source["X-WR-TIMEZONE"])
        for tz in source.walk("VTIMEZONE"):
            calendar.add_component(copy.deepcopy(tz))
        calendars[slug] = calendar
    for family in families.values():
        target = calendars[classify(family, config)]
        for event in family:
            target.add_component(copy.deepcopy(event))
    outputs = {slug: calendar.to_ical() for slug, calendar in calendars.items()}
    for data in outputs.values():
        # Refuse to publish if a future upstream field embeds the private feed URL.
        unfolded = data.replace(b"\r\n ", b"").replace(b"\r\n\t", b"")
        if re.search(rb"mytimetable\.tudelft\.nl/ical\?", unfolded, re.I):
            raise ValueError("Output contains a private source URL; publication stopped")
        Calendar.from_ical(data)
    assert sum(len(cal.walk("VEVENT")) for cal in calendars.values()) == len(events)
    return outputs


def download():
    url = os.environ.get("TIMETABLE_SOURCE_URL", "").strip().replace("\\&", "&")
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url:
        raise ValueError("Add the TIMETABLE_SOURCE_URL repository secret to enable live updates")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "mytimetable.tudelft.nl" or parsed.path != "/ical":
        raise ValueError("Expected an HTTPS TU Delft MyTimetable iCalendar URL")
    try:
        with urlopen(Request(url, headers={"User-Agent": "TimetableSplitter/1.0"}), timeout=45) as response:
            return response.read(MAX_BYTES + 1)
    except Exception:
        # Exceptions can contain full URLs. Never leak the source token to logs.
        raise ValueError("Timetable download failed; keeping previous feeds. Check the source link and retry") from None


def write_outputs(outputs, directory):
    directory.mkdir(parents=True, exist_ok=True)
    # Validate/serialize ALL feeds before changing any files. The workflow commits
    # them together only after successful completion.
    for slug, data in outputs.items():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            temporary.replace(directory / (slug + ".ics"))
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("filters.toml"))
    parser.add_argument("--input", type=Path, help="Local iCalendar for testing; never committed by the workflow")
    parser.add_argument("--output", type=Path, default=Path("feeds"))
    args = parser.parse_args()
    try:
        config = tomllib.loads(args.config.read_text())
        outputs = split_feed(args.input.read_bytes() if args.input else download(), config)
        write_outputs(outputs, args.output)
        for slug, data in outputs.items():
            print(f"{slug}: {len(Calendar.from_ical(data).walk('VEVENT'))} event records")
        return 0
    except Exception as error:
        if isinstance(error, ValueError) and str(error).startswith(("Add the TIMETABLE", "Timetable download failed")):
            print(str(error), file=sys.stderr)
        else:
            print("Split failed: invalid source, configuration, or output. Previous published feeds were not updated.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

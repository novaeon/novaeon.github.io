import copy
import os
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from icalendar import Alarm, Calendar, Event
from split_calendar import classify, download, split_feed, validate_config


CONFIG = tomllib.loads((Path(__file__).parents[1] / 'filters.toml').read_text())


def event(title, uid='one', description=''):
    item = Event()
    item.add('UID', uid)
    item.add('SUMMARY', title)
    item.add('DESCRIPTION', description)
    item.add('DTSTART', datetime(2026, 9, 7, 8, tzinfo=timezone.utc))
    item.add('DTEND', datetime(2026, 9, 7, 10, tzinfo=timezone.utc))
    item.add('LOCATION', 'Echo, Hall A')
    item.add('SEQUENCE', 4)
    return item


def source(*events):
    calendar = Calendar()
    calendar.add('PRODID', '-//Unit Test//EN')
    calendar.add('VERSION', '2.0')
    calendar.add('X-WR-CALNAME', 'Private source label')
    for item in events:
        calendar.add_component(item)
    return calendar.to_ical()


class SplitTests(unittest.TestCase):
    def test_routing(self):
        for title, expected in [
            ('CSE11A - Lecture', 'cse11a'), ('cse11b - Exam', 'cse11b'),
            ('CSE11C - Tutorial', 'cse11c'), ('CSE12A - Calculus', 'misc'),
            ('Mentorate', 'misc'), ('CSE11AA', 'misc'),
            ('CSE11A/CSE11B - Shared lab', 'misc'),
            ('CSE11A/CSE11B/CSE11C - Shared lab', 'misc'),
            ('CSE11A shared lab', 'misc'), ('CSE11A / CSE11B - Combined', 'misc'),
        ]:
            with self.subTest(title=title):
                self.assertEqual(classify([event(title)], CONFIG), expected)

    def test_description_only_course_code(self):
        self.assertEqual(classify([event('Lecture', description='Course code: CSE11B')], CONFIG), 'cse11b')

    def test_next_term_config_keeps_url_slot(self):
        config = copy.deepcopy(CONFIG)
        config['courses']['cse11a'] = {'name': 'Calculus', 'codes': ['CSE12A']}
        result = split_feed(source(event('CSE12A - Calculus')), config)
        self.assertEqual(len(Calendar.from_ical(result['cse11a']).walk('VEVENT')), 1)
        self.assertEqual(str(Calendar.from_ical(result['cse11a'])['X-WR-CALNAME']), 'Calculus')

    def test_no_dropped_or_duplicate_events_and_lossless_roundtrip(self):
        events = [event('CSE11A', 'a'), event('CSE11B', 'b'), event('CSE11C', 'c'), event('Shared lab', 'd')]
        results = split_feed(source(*events), CONFIG)
        recovered = [item for data in results.values() for item in Calendar.from_ical(data).walk('VEVENT')]
        self.assertCountEqual([item.to_ical() for item in events], [item.to_ical() for item in recovered])

    def test_recurrence_exception_cancellation_and_alarm(self):
        master = event('CSE11A', 'series')
        master.add('RRULE', {'FREQ': 'WEEKLY', 'COUNT': 4})
        alarm = Alarm()
        alarm.add('ACTION', 'DISPLAY')
        alarm.add('TRIGGER', timedelta(minutes=-15))
        alarm.add('DESCRIPTION', 'Reminder')
        master.add_component(alarm)
        override = event('Cancelled occurrence', 'series')
        override.add('RECURRENCE-ID', datetime(2026, 9, 14, 8, tzinfo=timezone.utc))
        override.add('STATUS', 'CANCELLED')
        result = split_feed(source(master, override), CONFIG)
        recovered = Calendar.from_ical(result['cse11a']).walk('VEVENT')
        self.assertEqual([item.to_ical() for item in recovered], [master.to_ical(), override.to_ical()])

    def test_html_truncated_and_empty_fail(self):
        for data in [b'<html>Error</html>', b'BEGIN:VCALENDAR\r\nVERSION:2.0', source()]:
            with self.assertRaises(ValueError):
                split_feed(data, CONFIG)

    def test_bad_config_fails(self):
        config = copy.deepcopy(CONFIG)
        config['courses']['../escape'] = config['courses'].pop('cse11a')
        with self.assertRaises(ValueError):
            validate_config(config)
        config = copy.deepcopy(CONFIG)
        config['courses']['cse11b']['codes'] = ['CSE11A']
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_empty_course_feed_valid(self):
        result = split_feed(source(event('Mentorate')), CONFIG)
        self.assertEqual(Calendar.from_ical(result['cse11a']).walk('VEVENT'), [])

    def test_source_metadata_not_copied(self):
        result = split_feed(source(event('Mentorate')), CONFIG)
        self.assertNotIn(b'Private source label', result['misc'])

    def test_token_never_in_download_error(self):
        url = 'https://mytimetable.tudelft.nl/ical?eu=private&h=private-token'
        with patch.dict(os.environ, {'TIMETABLE_SOURCE_URL': url}), patch('split_calendar.urlopen', side_effect=OSError(url)):
            with self.assertRaises(ValueError) as raised:
                download()
            self.assertNotIn('private-token', str(raised.exception))

    def test_private_url_in_event_blocks_publication(self):
        with self.assertRaises(ValueError):
            split_feed(source(event('CSE11A', description='https://mytimetable.tudelft.nl/ical?eu=secret')), CONFIG)


if __name__ == '__main__':
    unittest.main()

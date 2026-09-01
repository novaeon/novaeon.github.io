import unittest
from datetime import datetime, timezone

from scrape_calendar import Anchor, build_ics, parse_dates, parse_events


FIXTURE = """
<html><head>
  <meta property="article:modified_time" content="2026-08-25T16:05:53+00:00">
</head><body>
<div class="entry-content"></div>
<div class="entry-content">
  <img title="Monthposter Sep 2026" src="poster.png">
  <div class="brz-row__container">
    <p>Kelderjam</p><p>Friday September 4th</p><p>20:00-01:00</p>
    <p>Bring an instrument.</p><p>Free entry</p>
  </div>
  <div class="brz-row__container">
    <p>Open Monumenten Dagen</p>
    <p>Saturday September 12th and Friday September 13th</p>
    <p>10:00-18:00 | 12:00-18:00</p><p>Visit our building.</p>
  </div>
  <div class="brz-row__container">
    <p>More events TBA</p><p>We will announce more events soon.</p>
  </div>
</div>
</body></html>
"""


class ParserTests(unittest.TestCase):
    def test_single_and_multi_date_events(self):
        events, warnings, modified = parse_events(
            FIXTURE, datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(3, len(events))
        self.assertEqual("Kelderjam", events[0].title)
        self.assertEqual("20:00:00", events[0].start.isoformat())
        self.assertEqual("Open Monumenten Dagen", events[1].title)
        self.assertEqual("10:00:00", events[1].start.isoformat())
        self.assertEqual("12:00:00", events[2].start.isoformat())
        self.assertEqual(datetime(2026, 8, 25, 16, 5, 53, tzinfo=timezone.utc), modified)
        self.assertTrue(any("2026-09-13 is Sunday, not Friday" in warning for warning in warnings))

    def test_overnight_event_ends_next_day_in_ics(self):
        events, _, modified = parse_events(
            FIXTURE, datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        calendar = build_ics([events[0]], modified)
        # 20:00 and 01:00 Amsterdam summer time are 18:00 and 23:00 UTC.
        self.assertIn("DTSTART:20260904T180000Z", calendar)
        self.assertIn("DTEND:20260904T230000Z", calendar)

    def test_december_anchor_rolls_january_into_next_year(self):
        warnings = []
        parsed = parse_dates("Friday January 8th", Anchor(2026, 12), warnings)
        self.assertEqual("2027-01-08", parsed[0].isoformat())
        self.assertEqual([], warnings)


if __name__ == "__main__":
    unittest.main()

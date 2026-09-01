# Koornbeurs agenda calendar

This directory converts the [Koornbeurs agenda](https://koornbeurs.nl/agenda/)
into a public iCalendar feed for Google Calendar.

Calendar feed:

https://novaeon.github.io/koornbeurs-calendar/koornbeurs.ics

The GitHub Action in `.github/workflows/update-koornbeurs-calendar.yml` checks
the page every six hours. It uses deterministic HTML parsing, validates written
weekdays, handles events that end after midnight, and refuses to overwrite the
last working feed if the page layout can no longer be parsed.

To subscribe on desktop Google Calendar, open **Other calendars → + → From URL**
and paste the calendar-feed URL above.

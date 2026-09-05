# Colour-coded TU Delft timetable subscriptions

Subscribe separately to these feeds using Google Calendar **Other calendars → + → From URL**:

| Feed | Subscription URL |
| --- | --- |
| CSE11A | https://raw.githubusercontent.com/novaeon/novaeon.github.io/main/delft-calendar/feeds/cse11a.ics |
| CSE11B | https://raw.githubusercontent.com/novaeon/novaeon.github.io/main/delft-calendar/feeds/cse11b.ics |
| CSE11C | https://raw.githubusercontent.com/novaeon/novaeon.github.io/main/delft-calendar/feeds/cse11c.ics |
| Shared / Misc | https://raw.githubusercontent.com/novaeon/novaeon.github.io/main/delft-calendar/feeds/misc.ics |

Pick a different colour for each subscription. Hide or unsubscribe from the original combined calendar to avoid showing everything twice. Use subscriptions, not one-time file imports. HTTPS works in Google Calendar; clients that request webcal can use the same URL with `webcal://` in place of `https://`.

## One-time setup for automatic updates

The original MyTimetable access link is a secret, not part of the public code.

1. [Add a repository Actions secret](https://github.com/novaeon/novaeon.github.io/settings/secrets/actions/new) named **TIMETABLE_SOURCE_URL**.
2. Paste the entire original TU Delft iCalendar link as its value (including both query parameters). Use a plain URL, not Markdown. Save.
3. [Run Update Delft timetable feeds](https://github.com/novaeon/novaeon.github.io/actions/workflows/update-delft-calendar.yml) using **Run workflow**, or wait for the next six-hourly run.

The initial feeds are a snapshot. Until the secret is added, the workflow tests the splitter but skips live refreshing and displays a warning. Nothing fetches fresh timetable data until this is configured.

## Change courses next term

Edit [filters.toml](https://github.com/novaeon/novaeon.github.io/edit/main/delft-calendar/filters.toml).
For example change `name = "CSE11A — Reasoning & Logic"` to `name = "Calculus"` and
`codes = ["CSE11A"]` to `codes = ["CSE12A"]`. Commit to main.
Keep the section heading `[courses.cse11a]` unchanged to keep the same subscription URL;
the file ID is just a stable slot. Some calendar apps cache display names, so you may
also want to rename the subscription in the app. To add another feed, add a new
`[courses.your-id]` section with a name and a codes list, and subscribe to `your-id.ics`.

The configurable `shared_pattern` is checked first. An event matching it, more than one course,
or none of the configured courses goes into `misc`. Current shared lab titles match that pattern.
Exact, case-insensitive code matching prevents CSE11A from matching CSE11AA. Matching fields are
also configurable. Single-course tutorials and exams stay with that course. Unrecognised
future courses go into Misc until you configure them. Each event family goes into exactly one
feed. Recurrence exceptions remain with their parent series.

## Hosting and reliability

The recommended subscription URLs use GitHub raw files directly, so they reflect workflow
commits without needing a separate Pages deployment. Do not subscribe to the Pages copies:
[GitHub Actions commits using GITHUB_TOKEN do not trigger branch-based Pages builds](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

The job runs every six hours (GitHub schedules can be delayed). Calendar apps control their own
polling, so timetable changes will not appear instantly. Public repository schedules can be
disabled after 60 days of inactivity; check Actions if updates stop.

The splitter uses an iCalendar library and retains UID, start/end/timezone, recurrence rules,
exceptions, cancellation status, descriptions, room information and alarms. Each refresh replaces
the current snapshot; source-removed events are omitted from the next feed. No guessed times,
AI, or new event IDs. All outputs are validated before one commit publishes the complete set.
Empty source or malformed input fails without replacing the previous published data. An empty
individual course feed is allowed. Do not delete/rename a feed ID while still subscribed to it:
remove its subscription first; old feed files are not automatically deleted.

All generated timetable data is public, including its git history. Do not put the original
access-token link in code, config, issues or workflow inputs. For local tests:

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v
python split_calendar.py --input /path/to/private-source.ics --output /tmp/split-feeds
```

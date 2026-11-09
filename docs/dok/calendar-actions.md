# Calendar schedules and dok execution

## Ownership

- `CalendarEvent` remains the public local aggregate. Existing callers can pass
  `alarms` and `command`. Their field declarations and validation are inherited
  from `ActionSettings`; storage and drafts use that owner's codec/API.
- `CalendarStore` atomically stores schedule records, local action settings,
  external `ActionBinding` records and delivery receipts in `calendar.json` v4.
  They are separate domains in one locked transaction, not separate files with
  an unprotected cross-file commit. Existing v1–v3 calendar data is decoded by
  the store and written in the new structure on the next mutation.
- `ActionLink` identifies connection/calendar/event. An occurrence override also
  records its parent series. `ActionCatalog` resolves explicit overrides before
  series settings, including an explicit empty override.
- `CalendarScheduler` owns deadline calculation and future-only command admission.
  Storage persists its claims before `CalendarRuntime` performs effects.
- `ExternalActions` queries only schedules with explicit dok actions. The provider
  returns expanded occurrences; dok never expands a Google RRULE a second time.
- `GoogleRecurrence` owns Google RRULE field names and conversion. Unsupported
  provider rules remain opaque. UI and services do not parse Google dictionaries.
- `EventDraft` owns shared input, recurrence and reminder editing. `RemoteDraft`
  reuses it; `EventForm` renders both storage destinations. `CalendarPage` owns
  the fixed right-pane heading: selected date for list/editor, blank otherwise.
  `CalendarFormLayout` declares that row and its style; scrolling content and
  mouse/picker coordinates start below it.

## Editing and partial outcomes

Google schedule CRUD still uses an ETag and If-Match. `CalendarEdits.edit_target`
resolves an occurrence to its parent series before opening the editor; there is
no scope selection screen. Edit and delete always target the parent ID and ETag
for a recurrence. Missing or noneditable parents fail without falling back to
instance writes. A nonrepeating schedule keeps its original ID. The delete dialog
explicitly identifies an entire-series deletion. New recurrence uses Google RRULE
rather than posting independent copies. The UI exposes series actions only; the
catalog's typed occurrence-override interpretation remains a storage concern.

Google schedule persistence and local dok action persistence cannot form a single
atomic transaction. The provider write completes first. If the local write fails,
the UI retains the action update and offers a local-only retry; it does not resend
the Google mutation. Google conflicts and uncertain outcomes retain the draft and
require explicit review as before. A process exit between the two commits can
leave the Google schedule saved without the new local actions. Nothing replays
that write automatically on restart; inspect and save its dok settings explicitly.

Google's own cloud reminders are unchanged. dok reminders and commands never
come from imported description, title, attendee, URL or provider metadata. Only
explicitly saved local `ActionSettings` admit those effects.

## Background work and execution

The visible month retains its existing cache. With the calendar hidden, only
action-linked schedules are read. The source's `refresh_s` controls cadence.
Occurrence windows follow each reminder offset and the next refresh horizon,
including future reminders whose event is outside the visible month. Overlapping
windows and repeated instances are merged; all response pages must succeed.
No active action bindings means no background provider requests.

Source/account disablement withdraws its external candidates. A failed provider
read withdraws that source's candidates and retries after `refresh_s`, without
claiming the failure means an empty calendar. Action revision and connection
generation checks prevent an old response from re-arming previous settings.
Changes made elsewhere are observed at the next successful refresh; this is
polling, not push notifications or syncToken synchronization.

The same scheduler processes local schedules and verified external occurrences.
Reminders use persisted claims. External instance IDs remain delivery identities
even if titles, ETags or start times change: an already claimed command or offset
is not delivered again for that instance. Receipts survive restart and are not
bounded by an arbitrary item limit.

Commands are armed only for future starts after their schedule is observed.
Restart, unavailable reads and missed intervals do not replay old commands.
At a known start, a busy ish process or nonempty prompt skips that occurrence and
shows an explanation; it is not queued for later. Admission is persisted before
submission, so a crash between claim and effect can lose that execution. This is
at-most-once admission, not an exactly-once execution guarantee. ish must remain
running; no daemon or system scheduler is installed.

## Validation status

The user approved `tests/dok/reports/calendar-shared-actions-ui-20261109-r5/`.
Linux Python 3.12.14 dok-only tests: 452 discovered, 446 passed, 6 optional
Hub/llm checks skipped, no failures/errors. See [validation](validation.md) and
[the final report](../../tests/dok/reports/dok-3s4ygts0/summary.md).
Google HTTP was mocked. No real account access, personal WSL deployment/config
changes, ish/llm/hub edits, full llm/hub regression, commit or push occurred.

All-day timezone metadata uses the existing events scope through
[Events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list),
and is reused until the source refresh interval expires. External notification
start times are displayed in the configured clock timezone (system local by default).

"""Provider schedule draft sharing the local form's recurrence and action controls."""
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import os
from zoneinfo import ZoneInfo

from .actions import ActionBinding, ActionCatalog, ActionLink, ActionSettings
from .contracts import ExternalRecurrence
from .editor import EditorField, EventDraft
from .recurrence import Recurrence
from .writes import CalendarMutation, EventValues, WriteKind


def system_zone():
    """Resolve a named system zone for providers that require recurrence DST rules."""
    candidates = [os.environ.get("TZ", "").removeprefix(":")]
    localtime = Path("/etc/localtime").resolve()
    if "zoneinfo" in localtime.parts:
        candidates.append("/".join(localtime.parts[localtime.parts.index("zoneinfo") + 1:]))
    try:
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    for value in candidates:
        try:
            ZoneInfo(value)
            return value
        except (ValueError, KeyError):
            continue
    return None


class RemoteDraft(EventDraft):
    def __init__(self, source, day, zone, *, now=None, original=None, values=None, catalog=ActionCatalog()):
        self.source, self.zone = source, (original.timezone if original else None) or zone or system_zone()
        super().__init__(day, self.zone, now=now, choose_target=True)
        self.original = original
        self.event_id = original.id if original else CalendarMutation.new_id()
        self.link = ActionLink.for_event(source, original) if original else ActionLink(*source.identity, self.event_id)
        self.binding = catalog.get(self.link)
        self._settings = catalog.effective(self.link)
        self.alarms = self._settings.alarms
        self.buffers[EditorField.COMMAND].text = self._settings.command
        if original is not None:
            self._load(EventValues.from_event(original))
        self._initial = self.signature
        if values is not None:
            self._load(values)

    def _load(self, values):
        self.all_day = type(values.start) is date
        local = lambda value: value if type(value) is date else value.astimezone(ZoneInfo(self.zone) if self.zone else None)
        start, end = local(values.start), local(values.end)
        start_day, end_day = (start, end - timedelta(days=1)) if self.all_day else (start.date(), end.date())
        repeat = values.repeat.rule or Recurrence()
        self.unit, self.repeat_end = repeat.unit, repeat.end
        fields = {EditorField.TITLE: values.title, EditorField.NOTES: values.notes,
                  EditorField.START_DATE: start_day.isoformat(), EditorField.END_DATE: end_day.isoformat(),
                  EditorField.START_TIME: (time() if self.all_day else start.time()).isoformat(),
                  EditorField.END_TIME: (time() if self.all_day else end.time()).isoformat(),
                  EditorField.INTERVAL: str(repeat.interval), EditorField.REPEAT_COUNT: str(repeat.count),
                  EditorField.REPEAT_UNTIL: (repeat.until or start_day).isoformat()}
        for field, text in fields.items():
            self.buffers[field].text = text

    @property
    def signature(self):
        return super().signature, self.source.identity

    def _aware(self, value, original=None):
        if isinstance(original, datetime):
            local = original.astimezone(ZoneInfo(self.zone) if self.zone else None)
            if local.replace(tzinfo=None) == value:
                return original
        aware = value.replace(tzinfo=ZoneInfo(self.zone)) if self.zone else value.astimezone()
        if aware.astimezone(timezone.utc).astimezone(aware.tzinfo).replace(tzinfo=None) != value:
            raise ValueError("The selected time does not exist")
        return aware

    def supports(self, field):
        if field == EditorField.HOLIDAY:
            return False
        if field in (EditorField.INTERVAL, EditorField.REPEAT, EditorField.REPEAT_END,
                     EditorField.REPEAT_COUNT, EditorField.REPEAT_UNTIL):
            if self.original is not None and (self.original.series_id or not self.original.repeat.editable):
                return False
        return super().supports(field)

    def temporal(self, field):
        return self.temporal_value(field)

    def change_source(self, source):
        if self.original is not None:
            raise ValueError("An existing schedule cannot change its destination")
        self.source = source
        self.link = ActionLink(*source.identity, self.event_id)

    def choose(self, field, value):
        self.choose_temporal(field, value)

    def values(self):
        start, end = self.boundaries()
        if self.all_day:
            start, end = start.date(), end.date()
        else:
            start = self._aware(start, self.original.start if self.original else None)
            end = self._aware(end, self.original.end if self.original else None)
        repeat = (ExternalRecurrence(self.recurrence()) if self.supports(EditorField.REPEAT) else
                  self.original.repeat if self.original else ExternalRecurrence())
        return EventValues(self.buffers[EditorField.TITLE].text, start, end,
                           self.original.location if self.original else "", self.buffers[EditorField.NOTES].text,
                           repeat, self.zone)

    def action_change(self):
        settings = ActionSettings(alarms=self.alarms, command=self.buffers[EditorField.COMMAND].text)
        if settings == self._settings:
            return None
        return ActionBinding(self.link, settings, self.binding.revision if self.binding else 0)

    def mutation(self, *, delete=False):
        kind = WriteKind.DELETE if delete else WriteKind.UPDATE if self.original else WriteKind.CREATE
        return CalendarMutation(self.source, kind, self.event_id, None if delete else self.values(), self.original)

    def rebased(self, latest, day, *, keep=False):
        values = self.values() if keep else None
        if keep and self.original is not None:
            values = values.rebase(EventValues.from_event(self.original), EventValues.from_event(latest))
        catalog = ActionCatalog((ActionBinding(self.link, self._settings, self.binding.revision if self.binding else 0),))
        result = RemoteDraft(self.source, day, self.zone, original=latest, values=values, catalog=catalog)
        if keep:
            result.alarms = self.alarms
            result.buffers[EditorField.COMMAND].text = self.buffers[EditorField.COMMAND].text
        return result

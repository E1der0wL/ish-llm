"""Local calendar draft; persisted validation is owned by CalendarEvent."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from .models import CalendarEvent, Recurrence, RepeatUnit, LocalDateTime
from ...locales import Locale, Message as M


class EditorField(StrEnum):
    TITLE = "title"
    ALL_DAY = "all_day"
    START_DATE = "start_date"
    END_DATE = "end_date"
    START_TIME = "start_time"
    END_TIME = "end_time"
    LOCATION = "location"
    ALARM = "alarm"
    INTERVAL = "interval"
    REPEAT = "repeat"
    HOLIDAY = "holiday"
    NOTES = "notes"
    SAVE = "save"
    CANCEL = "cancel"
    DELETE = "delete"

    @property
    def label(self):
        return self.translated(Locale())

    def translated(self, locale):
        return locale.text({self.TITLE: M.TITLE, self.ALL_DAY: M.TIME, self.START_DATE: M.START_DATE, self.END_DATE: M.END_DATE,
                self.START_TIME: M.START_TIME, self.END_TIME: M.END_TIME,
                self.LOCATION: M.LOCATION, self.ALARM: M.ALARM, self.INTERVAL: M.INTERVAL,
                self.REPEAT: M.REPEAT, self.HOLIDAY: M.HOLIDAY, self.NOTES: M.NOTES,
                self.SAVE: M.SAVE, self.CANCEL: M.CANCEL, self.DELETE: M.DELETE}[self])


class EventDraft:
    def __init__(self, day, timezone, event=None):
        self.original = event
        start = event.start if event else datetime.combine(day, datetime.min.time()).replace(hour=9)
        end = event.end if event else start + timedelta(hours=1)
        self.timezone = event.timezone if event else timezone
        self.all_day = event.all_day if event else False
        self.holiday = event.holiday if event else False
        self.unit = event.repeat.unit if event else RepeatUnit.NONE
        visible_end = end - timedelta(days=1) if self.all_day else end
        values = {EditorField.TITLE: event.title if event else "",
                  EditorField.START_DATE: start.date().isoformat(), EditorField.END_DATE: visible_end.date().isoformat(),
                  EditorField.START_TIME: start.time().isoformat(timespec="auto" if start.second or start.microsecond else "minutes"),
                  EditorField.END_TIME: end.time().isoformat(timespec="auto" if end.second or end.microsecond else "minutes"),
                  EditorField.LOCATION: event.location if event else "",
                  EditorField.ALARM: LocalDateTime.edit_text(event.alarm) if event and event.alarm else "",
                  EditorField.INTERVAL: str(event.repeat.interval if event else 1), EditorField.NOTES: event.notes if event else ""}
        self.buffers = {field: Buffer(document=Document(text, len(text)), multiline=field == EditorField.NOTES) for field, text in values.items()}
        self._initial = self.signature

    @property
    def signature(self):
        return (tuple((key, buffer.text) for key, buffer in self.buffers.items()), self.all_day, self.holiday, self.unit)

    @property
    def dirty(self):
        return self.signature != self._initial

    def buffer(self, field):
        """Return the text editing API for a field, or None for a button."""
        return self.buffers.get(field)

    def fields(self):
        """Visible focus order; hidden time inputs cannot receive focus."""
        return tuple(field for field in EditorField
                     if not (self.all_day and field in (EditorField.START_TIME, EditorField.END_TIME)))

    def toggle_all_day(self):
        self.all_day = not self.all_day

    def event(self):
        def text(field):
            return self.buffers[field].text
        start_day, end_day = date.fromisoformat(text(EditorField.START_DATE)), date.fromisoformat(text(EditorField.END_DATE))
        if self.all_day:
            # The editor shows inclusive dates; storage owns exclusive end times.
            start = datetime.combine(start_day, time())
            try:
                end = datetime.combine(end_day + timedelta(days=1), time())
            except OverflowError as exc:
                raise ValueError("All-day end cannot extend beyond Python's supported date range") from exc
        else:
            start = LocalDateTime().decode(datetime.combine(start_day, time.fromisoformat(text(EditorField.START_TIME))))
            end = LocalDateTime().decode(datetime.combine(end_day, time.fromisoformat(text(EditorField.END_TIME))))
        event = CalendarEvent(title=text(EditorField.TITLE), start=start, end=end, timezone=self.timezone,
            all_day=self.all_day, location=text(EditorField.LOCATION),
            alarm=LocalDateTime().decode(text(EditorField.ALARM)) if text(EditorField.ALARM).strip() else None,
            repeat=Recurrence.from_input(self.unit, text(EditorField.INTERVAL)), notes=text(EditorField.NOTES), holiday=self.holiday)
        return replace(event, id=self.original.id, revision=self.original.revision) if self.original else event

    def edit(self, field, key, data):
        buffer = self.buffers.get(field)
        if buffer is None:
            return False
        if key in ("<any>", "any", "<bracketed-paste>"):
            text = data if field == EditorField.NOTES else data.replace("\n", " ").replace("\r", " ")
            if text and (key == "<bracketed-paste>" or all(char >= " " for char in text)):
                buffer.save_to_undo_stack()
                buffer.insert_text(text)
        elif key in ("backspace", "c-h"):
            buffer.save_to_undo_stack()
            buffer.delete_before_cursor()
        elif key == "delete":
            buffer.save_to_undo_stack()
            buffer.delete()
        elif key == "left":
            buffer.cursor_left()
        elif key == "right":
            buffer.cursor_right()
        elif key == "up":
            buffer.cursor_up()
        elif key == "down":
            buffer.cursor_down()
        elif key in ("home", "c-a"):
            buffer.cursor_position += buffer.document.get_start_of_line_position()
        elif key in ("end", "c-e"):
            buffer.cursor_position += buffer.document.get_end_of_line_position()
        elif key == "c-u":
            buffer.delete_before_cursor(count=buffer.cursor_position)
        elif key == "c-k":
            buffer.delete(count=len(buffer.text) - buffer.cursor_position)
        elif key == "c-z":
            buffer.undo()
        elif key == "c-y":
            buffer.redo()
        elif key == "enter" and field == EditorField.NOTES:
            buffer.insert_text("\n")
        else:
            return False
        return True

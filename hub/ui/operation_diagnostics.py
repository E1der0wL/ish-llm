"""Bounded, project-scoped diagnostics for UI operations in this Hub process."""

from hub.locales.keys import MessageKey

from collections import deque
from dataclasses import asdict
from datetime import datetime
import json

from ..errors.adapters import from_exception
from ..model import HubSnapshot
from .input.json_document import JsonDocument
from .error_presentation import plain


class OperationDiagnostics:
    MAX_RECORDS = 32
    MAX_DETAIL_CHARS = 20000
    EMPTY_TEXT = ""
    LINE = "\n"
    ENTRY_SEPARATOR = "\n\n"
    DETAIL_PREFIX = "    "
    INDENT = JsonDocument.INDENT
    ENSURE_ASCII = JsonDocument.ENSURE_ASCII

    def __init__(self):
        self.records = deque(maxlen=self.MAX_RECORDS)

    def record(self, project_id, error):
        info = from_exception(error)
        self.records.append((project_id, datetime.now().astimezone().isoformat(), info))
        return info

    def record_view(self, view, error):
        return self.record(
            HubSnapshot.project_identifier(view), error
        )

    def text(self, project_id, t):
        entries = []
        for project, stamp, error in self.records:
            if project == project_id:
                body = json.dumps(
                    asdict(error),
                    ensure_ascii=self.ENSURE_ASCII,
                    indent=self.INDENT,
                    default=str,
                )
                entries.append(
                    stamp
                    + self.LINE
                    + self.LINE.join(
                        self.DETAIL_PREFIX + line
                        for line in plain(body, self.MAX_DETAIL_CHARS).splitlines()
                    )
                )
        return (
            (
                t(MessageKey.OPERATION_DIAGNOSTICS)
                + self.ENTRY_SEPARATOR
                + self.ENTRY_SEPARATOR.join(entries)
            )
            if entries
            else self.EMPTY_TEXT
        )

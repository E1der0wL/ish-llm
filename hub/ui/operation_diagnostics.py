"""Bounded, project-scoped diagnostics for UI operations in this Hub process."""

from hub.locales.keys import MessageKey

from collections import deque
from dataclasses import asdict
from datetime import datetime
import json

from ..errors.adapters import from_exception
from .error_presentation import plain


class OperationDiagnostics:
    def __init__(self):
        self.records = deque(maxlen=32)

    def record(self, project_id, error):
        info = from_exception(error)
        self.records.append((project_id, datetime.now().astimezone().isoformat(), info))
        return info

    def text(self, project_id, t):
        entries = []
        for project, stamp, error in self.records:
            if project == project_id:
                body = json.dumps(asdict(error), ensure_ascii=False, indent=2, default=str)
                entries.append(stamp + "\n" + "\n".join("    " + line for line in plain(body, 20000).splitlines()))
        return (t(MessageKey.OPERATION_DIAGNOSTICS) + "\n\n" + "\n\n".join(entries)) if entries else ""

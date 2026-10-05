"""Validated global language, notification, startup and UI preferences."""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import shlex


@dataclass(frozen=True, slots=True)
class GeneralSettings:
    editor: str = ""
    language: str = ""
    language_packs: dict[str, dict[str, str]] = field(default_factory=dict)
    notification_kinds: list[str] = field(default_factory=lambda: ["error", "info", "warning", "success"])
    notification_seconds: float = 8
    restore_last_project: bool = True
    restore_last_session: bool = True
    auto_scroll: bool = True

    def __post_init__(self):
        from ..locales import Language, validate_packs
        validate_packs(self.language_packs)
        if self.language:
            Language(self.language, self.language_packs)
        elif not isinstance(self.language, str):
            raise ValueError("language must be a string")
        if (not isinstance(self.notification_kinds, list) or
                any(kind not in ("error", "info", "warning", "success") for kind in self.notification_kinds) or
                len(set(self.notification_kinds)) != len(self.notification_kinds)):
            raise ValueError("notification_kinds must list unique error/info/warning/success values")
        if (type(self.notification_seconds) not in (int, float) or not math.isfinite(self.notification_seconds) or
                not 1 <= self.notification_seconds <= 120):
            raise ValueError("notification_seconds must be between 1 and 120")
        for key in ("restore_last_project", "restore_last_session", "auto_scroll"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        if not isinstance(self.editor, str):
            raise ValueError("editor must be a command string")
        if self.editor.strip():
            argv = shlex.split(self.editor)
            if not argv or not argv[0]:
                raise ValueError("editor must name an executable")

    def editor_argv(self, path: str | Path) -> list[str]:
        """Resolve an argument vector; never interpret the command as shell code."""
        command = self.editor.strip() or os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        argv = shlex.split(command)
        if not argv or not argv[0]:
            raise ValueError("editor must name an executable")
        return [*argv, str(Path(path).resolve())]

"""Validated global language, notification, startup and UI preferences."""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import shlex


@dataclass(frozen=True, slots=True)
class GeneralSettings:
    editor: str = ""
    language: str = field(default="", metadata={"requires_restart": True})
    language_packs: dict[str, dict[str, str]] = field(default_factory=dict, metadata={"requires_restart": True})
    notification_kinds: list[str] = field(default_factory=lambda: ["error", "info", "warning", "success"])
    notification_seconds: float = 8
    restore_last_project: bool = field(default=True, metadata={"requires_restart": True})
    restore_last_session: bool = field(default=True, metadata={"requires_restart": True})
    auto_scroll: bool = True
    workspace_root: str = field(default="", metadata={"requires_restart": True})
    provider_request_summary: bool = field(default=True, metadata={"requires_restart": True})
    provider_max_detail_chars: int = field(default=2000, metadata={"requires_restart": True})
    recovery_enabled: bool = field(default=True, metadata={"requires_restart": True})
    recovery_model_analysis: bool = field(default=True, metadata={"requires_restart": True})
    recovery_timeout_seconds: float = field(default=60, metadata={"requires_restart": True})
    recovery_input_chars: int = field(default=12000, metadata={"requires_restart": True})
    recovery_summary_chars: int = field(default=2000, metadata={"requires_restart": True})
    recovery_max_summary_calls: int = field(default=16, metadata={"requires_restart": True})

    def __post_init__(self):
        from .workspace import workspace_path
        workspace_path(self.workspace_root)
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
        for key in ("restore_last_project", "restore_last_session", "auto_scroll",
                    "provider_request_summary", "recovery_enabled", "recovery_model_analysis"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        if type(self.provider_max_detail_chars) is not int or self.provider_max_detail_chars < 1:
            raise ValueError("provider_max_detail_chars must be a positive integer")
        for key in ("recovery_input_chars", "recovery_summary_chars", "recovery_max_summary_calls"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f"{key} must be a positive integer")
        if (type(self.recovery_timeout_seconds) not in (int, float) or
                not math.isfinite(self.recovery_timeout_seconds) or self.recovery_timeout_seconds <= 0):
            raise ValueError("recovery_timeout_seconds must be positive and finite")
        if self.recovery_input_chars <= self.recovery_summary_chars + 1000:
            raise ValueError("recovery_input_chars must leave space for the summary and payload")
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

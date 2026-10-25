"""Validated global language, notification, startup and UI preferences."""

from dataclasses import dataclass, field
from typing import ClassVar

from llm.core.schema import SchemaKeyword, SchemaType
from .metadata import SettingMetadata
from .notifications import NotificationKind
import math
import os
from pathlib import Path
import shlex


class EditorCommand:
    VISUAL_ENVIRONMENT = "VISUAL"
    EDITOR_ENVIRONMENT = "EDITOR"
    FALLBACK_EXECUTABLE = "vi"
    INVALID = "editor must be a command string"
    MISSING_EXECUTABLE = "editor must name an executable"

    @classmethod
    def arguments(cls, command):
        argv = shlex.split(command)
        if not argv or not argv[0]:
            raise ValueError(cls.MISSING_EXECUTABLE)
        return argv


@dataclass(frozen=True, slots=True)
class GeneralSettings:
    AUTO_LANGUAGE: ClassVar[str] = ""
    NOTIFICATION_KINDS: ClassVar[tuple[str, ...]] = tuple(NotificationKind.values())
    MINIMUM_NOTIFICATION_SECONDS: ClassVar[int] = 1
    MAXIMUM_NOTIFICATION_SECONDS: ClassVar[int] = 120
    MINIMUM_POSITIVE_INTEGER: ClassVar[int] = 1
    MINIMUM_RECOVERY_SECONDS_EXCLUSIVE: ClassVar[int] = 0
    MINIMUM_PAYLOAD_CHARS: ClassVar[int] = 1000
    INVALID_LANGUAGE: ClassVar[str] = "language must be a string"
    INVALID_NOTIFICATION_KINDS: ClassVar[str] = (
        "notification_kinds must list unique error/info/warning/success values"
    )
    INVALID_NOTIFICATION_SECONDS: ClassVar[str] = (
        "notification_seconds must be between 1 and 120"
    )
    INVALID_BOOLEAN: ClassVar[str] = "{key} must be a boolean"
    INVALID_POSITIVE_INTEGER: ClassVar[str] = "{key} must be a positive integer"
    INVALID_RECOVERY_SECONDS: ClassVar[str] = (
        "recovery_timeout_seconds must be positive and finite"
    )
    INVALID_PAYLOAD_CAPACITY: ClassVar[str] = (
        "recovery_input_chars must leave space for the summary and payload"
    )

    editor: str = ""
    language: str = field(
        default=AUTO_LANGUAGE,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    language_packs: dict[str, dict[str, str]] = field(
        default_factory=dict,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    notification_kinds: list[str] = field(
        default_factory=lambda: list(GeneralSettings.NOTIFICATION_KINDS),
        metadata={
            SettingMetadata.SCHEMA: {
                SchemaKeyword.ITEMS: {
                    SchemaKeyword.TYPE: SchemaType.STRING,
                    SchemaKeyword.ENUM: list(NOTIFICATION_KINDS),
                }
            }
        },
    )
    notification_seconds: float = field(
        default=8,
        metadata={
            SettingMetadata.SCHEMA: {
                SchemaKeyword.MINIMUM: MINIMUM_NOTIFICATION_SECONDS,
                SchemaKeyword.MAXIMUM: MAXIMUM_NOTIFICATION_SECONDS,
            }
        },
    )
    restore_last_project: bool = field(
        default=True,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    restore_last_session: bool = field(
        default=True,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    auto_scroll: bool = True
    workspace_root: str = field(
        default="", metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART}
    )
    provider_request_summary: bool = field(
        default=True,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    provider_max_detail_chars: int = field(
        default=2000,
        metadata={
            SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART,
            SettingMetadata.SCHEMA: {SchemaKeyword.MINIMUM: MINIMUM_POSITIVE_INTEGER},
        },
    )
    recovery_enabled: bool = field(
        default=True,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    recovery_model_analysis: bool = field(
        default=True,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    recovery_timeout_seconds: float = field(
        default=60,
        metadata={
            SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART,
            SettingMetadata.SCHEMA: {
                SchemaKeyword.EXCLUSIVE_MINIMUM: MINIMUM_RECOVERY_SECONDS_EXCLUSIVE
            },
        },
    )
    recovery_input_chars: int = field(
        default=12000,
        metadata={
            SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART,
            SettingMetadata.SCHEMA: {
                SchemaKeyword.EXCLUSIVE_MINIMUM: MINIMUM_POSITIVE_INTEGER - 1
            },
        },
    )
    recovery_summary_chars: int = field(
        default=2000,
        metadata={
            SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART,
            SettingMetadata.SCHEMA: {
                SchemaKeyword.EXCLUSIVE_MINIMUM: MINIMUM_POSITIVE_INTEGER - 1
            },
        },
    )
    recovery_max_summary_calls: int = field(
        default=16,
        metadata={
            SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART,
            SettingMetadata.SCHEMA: {
                SchemaKeyword.EXCLUSIVE_MINIMUM: MINIMUM_POSITIVE_INTEGER - 1
            },
        },
    )

    def resolved_language(self, fallback):
        return fallback if self.language == self.AUTO_LANGUAGE else self.language


    def __post_init__(self):
        from .workspace import workspace_path

        workspace_path(self.workspace_root)
        from ..locales import Language, validate_packs

        validate_packs(self.language_packs)
        if not isinstance(self.language, str):
            raise ValueError(self.INVALID_LANGUAGE)
        if self.language != self.AUTO_LANGUAGE:
            Language(self.language, self.language_packs)
        if (
            not isinstance(self.notification_kinds, list)
            or any(
                kind not in self.NOTIFICATION_KINDS for kind in self.notification_kinds
            )
            or len(set(self.notification_kinds)) != len(self.notification_kinds)
        ):
            raise ValueError(self.INVALID_NOTIFICATION_KINDS)
        if (
            type(self.notification_seconds) not in (int, float)
            or not math.isfinite(self.notification_seconds)
            or not self.MINIMUM_NOTIFICATION_SECONDS
            <= self.notification_seconds
            <= self.MAXIMUM_NOTIFICATION_SECONDS
        ):
            raise ValueError(self.INVALID_NOTIFICATION_SECONDS)
        for key in SettingMetadata.names_of_type(GeneralSettings, bool):
            if type(getattr(self, key)) is not bool:
                raise ValueError(self.INVALID_BOOLEAN.format(key=key))
        for key in SettingMetadata.names_of_type(GeneralSettings, int):
            if (
                type(getattr(self, key)) is not int
                or getattr(self, key) < self.MINIMUM_POSITIVE_INTEGER
            ):
                raise ValueError(self.INVALID_POSITIVE_INTEGER.format(key=key))
        if (
            type(self.recovery_timeout_seconds) not in (int, float)
            or not math.isfinite(self.recovery_timeout_seconds)
            or self.recovery_timeout_seconds <= self.MINIMUM_RECOVERY_SECONDS_EXCLUSIVE
        ):
            raise ValueError(self.INVALID_RECOVERY_SECONDS)
        if (
            self.recovery_input_chars
            <= self.recovery_summary_chars + self.MINIMUM_PAYLOAD_CHARS
        ):
            raise ValueError(self.INVALID_PAYLOAD_CAPACITY)
        if not isinstance(self.editor, str):
            raise ValueError(EditorCommand.INVALID)
        if self.editor.strip():
            EditorCommand.arguments(self.editor)

    def editor_argv(self, path: str | Path) -> list[str]:
        """Resolve an argument vector; never interpret the command as shell code."""
        command = (
            self.editor.strip()
            or os.environ.get(EditorCommand.VISUAL_ENVIRONMENT)
            or os.environ.get(EditorCommand.EDITOR_ENVIRONMENT)
            or EditorCommand.FALLBACK_EXECUTABLE
        )
        argv = EditorCommand.arguments(command)
        return [*argv, str(Path(path).resolve())]

    @classmethod
    def schema_properties(cls):
        return SettingMetadata.properties(cls)


class LanguagePacksDraft:
    """A bound edit target; callbacks retain the mapping selected by their form."""

    def __init__(self, value):
        self._value = value

    def __bool__(self):
        return bool(self._value)

    def names(self):
        return tuple(self._value)

    def remove(self, code):
        self._value.pop(code)


class GeneralSettingsDraft:
    """Editable preferences; complete stored sections still use the strict store Reader."""

    def __init__(self, value):
        self._value = value

    @property
    def workspace_selection(self):
        return self._value.get(GeneralSettings.workspace_root.__name__,
                               SettingMetadata.initial(GeneralSettings, GeneralSettings.workspace_root))

    @staticmethod
    def same_language_packs(left, right):
        return left.get(GeneralSettings.language_packs.__name__, {}) == right.get(GeneralSettings.language_packs.__name__, {})

    def initialize_packs(self):
        self._value.setdefault(GeneralSettings.language_packs.__name__, {})

    def pack_names(self):
        return tuple(self._value[GeneralSettings.language_packs.__name__])

    def packs(self):
        return LanguagePacksDraft(self._value[GeneralSettings.language_packs.__name__])

    def set_pack(self, code, messages):
        from copy import deepcopy

        self._value[GeneralSettings.language_packs.__name__][code] = deepcopy(messages)
